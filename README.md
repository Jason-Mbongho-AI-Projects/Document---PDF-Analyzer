# DocIntel

A PDF workspace: read, understand, edit, sign, secure and convert documents in
the browser.

FastAPI backend · React + PDF.js frontend · background worker
**109 API operations across 96 endpoints · 730 tests**

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Request lifecycle](#request-lifecycle)
- [Data model](#data-model)
- [Provider seams](#provider-seams)
- [Security model](#security-model)
- [Running it](#running-it)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Testing](#testing)
- [Dependencies and licensing](#dependencies-and-licensing)
- [Things this project is deliberately honest about](#things-this-project-is-deliberately-honest-about)
- [Not built](#not-built)

---

## What it does

Tools are grouped by the job they do, which is how the interface presents them.
`Ctrl`/`Cmd`+`K` jumps to any tool by name.

| Group | Tools |
|---|---|
| **Review** | Comments and mark-up, search, compare two documents, version history |
| **Understand** | Summarise, analyse, ask questions with page citations, translate |
| **Edit** | Edit text, page operations, stamping, insert pages, combine |
| **Forms** | Fill a form, build a form, sign or request signatures |
| **Secure** | Security scan, redact, properties and hidden data, links and attachments |
| **Convert** | Export to nine formats, OCR |

**Read** — PDF.js viewer with a real text layer, page thumbnails, search with
highlight rectangles, snapshot any region to an image, measure distances in
mm, cm, inches or points.

**Understand** — whole-document summaries in four styles, structured analysis,
key quotes, keywords, sentiment and readability, questions answered with page
citations, translation with a shared glossary.

**Edit the text** — change, delete or add words on the page, choosing font,
size, colour and weight.

**Edit the pages** — rotate, reorder by drag, delete, duplicate, extract,
crop, split, combine, insert or replace pages from another document, blank
pages, watermark, page numbers, headers and footers, Bates numbering,
compression, background.

**Mark it up** — highlight, underline, strike through, boxes, arrows,
freehand, text boxes and notes, in five colours.

**Forms and signing** — detect and fill existing forms, build fillable ones by
dragging fields, Fill & Sign for yourself, or a multi-party signature request
with sequential order, decline handling and an audit trail.

**Secure** — static security scanner, verified redaction, AES-256 protection
and authorised unlock, document properties, a report of the hidden data a file
carries and a way to strip it.

**Convert** — PDF to text, Markdown, HTML, JSON, CSV, XLSX, DOCX, PPTX and
images; text, Markdown, CSV, images and office files to PDF; OCR for scans;
deskew, despeckle and contrast for poor ones.

**On a phone** — the thumbnail rail and tools panel become drawers rather than
disappearing, the first page fits the viewport, and every drag gesture works
by touch.

---

## Architecture

Three processes. Nothing shares memory; they meet at the database and the
object store.

```
                    ┌──────────────────────────────┐
   browser ────────▶│  Frontend  (Vite, port 5173) │
                    │  React 18 · PDF.js           │
                    │  renders, selects, gestures  │
                    └───────────────┬──────────────┘
                                    │  /api/v1  (proxied in dev)
                                    ▼
                    ┌──────────────────────────────┐
                    │  API  (FastAPI, port 8000)   │
                    │  109 operations, 10 routers  │
                    │  auth · authz · audit        │
                    └───┬───────────────────┬──────┘
                        │                   │
              ┌─────────▼────────┐   ┌──────▼─────────┐
              │  Database        │   │  Object store  │
              │  SQLite/Postgres │   │  disk / S3     │
              │  17 tables       │   │  PDF bytes     │
              └─────────▲────────┘   └──────▲─────────┘
                        │                   │
                    ┌───┴───────────────────┴──────┐
                    │  Worker                      │
                    │  ingest · security scan      │
                    │  claims jobs from the queue  │
                    └──────────────────────────────┘
```

### Why the split

**PDF work is server-side** because the libraries that do it are native:
pypdf and pikepdf for structure, PDFium for rasterising, reportlab for
drawing, Tesseract for OCR, LibreOffice for office files. None of that exists
in a browser.

**Rendering is client-side** because PDF.js keeps text selectable on real
positioned glyphs and stays sharp at any zoom, which a server-rendered image
cannot.

**Slow work is a worker** so an upload returns immediately. Profiling a
document and scanning it for risky constructs happen behind a job queue.

### Backend layout

`docintel/` — 59 modules, ~13,300 lines.

| Package | Responsibility |
|---|---|
| `api/v1/` | HTTP surface: `documents` `edit` `content` `annotations` `ai` `convert` `signing` `workspaces` `auth` `jobs` |
| `pdf/` | Every PDF operation: `operations` `text` `textedit` `render` `redact` `forms` `formbuilder` `convert` `compare` `ocr` `ocr_layer` `annots` `assemble` `properties` `links` `attachments` `enhance` `engine` |
| `ai/` | `provider` (LLM abstraction) `service` `analysis` `insights` `translate` |
| `jobs/` | `queue` (claiming) `worker` (loop) `handlers` (the work) |
| `db/` | `models` `session` |
| `core/` | `deps` (auth and authorization) `security` `audit` `uploads` |
| `services/` | `documents` — versioning, the one path that writes new bytes |
| `storage/` | `StorageProvider`, local and S3 |
| `signing/` | Signature request state machine and audit trail |

### Frontend layout

`frontend/src/` — 32 files.

| Path | Responsibility |
|---|---|
| `views/` | `Library` (documents) `Workspace` (the editor) `SigningView` (public) |
| `components/` | `PdfPage` (canvas, text layer, overlays, drawing) `Thumbnail` `CommandPalette` and one panel per tool group |
| `api.ts` | Typed client, token handling, reachability reporting |
| `localSearch.ts` | Search in the browser, over text PDF.js already decoded |
| `useApiHealth.ts` | Whether the server is reachable, and what still works if not |
| `useDraft.ts` | Autosave for work that only exists in the browser |

---

## Request lifecycle

**Upload.** Bytes are validated by magic number, not filename or declared
type. The document row, version 1 and the stored object are written, then two
jobs are queued: `INGEST` and `SECURITY_SCAN`. The response returns before
either runs.

**An edit.** Every operation follows the same shape:

```
authorise → read a version → transform the bytes → verify the result
          → append a new version → write an audit row → return
```

Nothing is edited in place. `services/documents.add_version` is the only path
that writes new bytes, which is why version history is complete rather than
best-effort.

**Verification** is part of the operation, not a separate check. Redaction
re-parses its output and fails if the text is still extractable. OCR fails if
its text layer cannot be read back. A text edit fails if the old text
survives. The result is discarded rather than returned.

**Jobs** are claimed with a conditional `UPDATE` that re-asserts the queued
state, so two workers cannot take the same job without a broker. Stalled jobs
are reaped and retried up to three times.

---

## Data model

17 tables.

| Group | Tables |
|---|---|
| Tenancy | `organizations` `workspaces` `workspace_members` `users` |
| Documents | `documents` `document_versions` `document_annotations` `document_analyses` `document_security_findings` |
| Signing | `signature_requests` `signature_recipients` `signature_fields` `signature_events` `signature_assets` |
| Operations | `processing_jobs` `audit_logs` `usage_records` |

**Versions are content-addressed.** Identical bytes are stored once; the
version row records the hash. Restoring an earlier version appends a new one,
so a restore can itself be undone.

**Annotations are rows, not PDF objects.** Marking up never rewrites the file,
and several people can comment without contending over bytes. A flattened copy
is produced on request.

---

## Provider seams

Five abstract interfaces mark where an implementation can be swapped:

| Interface | Implementations |
|---|---|
| `PDFEngine` | pypdf/pikepdf |
| `StorageProvider` | local disk, S3 (and S3-compatible) |
| `JobQueue` | database |
| `OCRProvider` | Tesseract, or a null provider reporting why |
| `LLMProvider` | OpenRouter |

Selecting a driver that does not exist fails at startup naming what is
available, rather than degrading quietly at the first request.

---

## Security model

- Bearer tokens, bcrypt at 12 rounds, JWT with a pinned algorithm.
- **Object-level authorization on every document route**, joining through
  workspace membership. Cross-tenant reads return **404, not 403**, so ids
  cannot be enumerated. Operations that name a second document — combine,
  insert pages, replace pages — authorise each one separately.
- Uploads validated by magic bytes.
- Extracted text is fenced in unguessable delimiters before reaching a model,
  and the system prompt states it is data, never instructions.
- Signature assets are per-user and never served from a public URL.
- Audit logs record identifiers and outcomes, never document content.
- Links are restricted to `http`, `https` and `mailto`; attachments refuse
  executables and strip any path from the filename.

`DOCINTEL_AUTH_MODE=open` disables **authentication** for local development.
Authorization is unaffected — the dev user is a real user in a real workspace,
so tenant isolation still applies and is tested in that mode. The server
**refuses to start** with it in production.

---

## Running it

Three processes. Python 3.13, Node 20+.

```bash
# install
python -m venv .venv
.venv\Scripts\pip install -r requirements-platform.txt   # Windows
cd frontend && npm install && cd ..

# configure
copy .env.example .env                                   # then add your key

# migrate
.venv\Scripts\python -m alembic upgrade head
```

```bash
# terminal 1 — API           http://127.0.0.1:8000   (docs at /docs)
.venv\Scripts\python -m uvicorn docintel.main:app --port 8000

# terminal 2 — worker
.venv\Scripts\python -m docintel.jobs.worker

# terminal 3 — frontend      http://localhost:5173
cd frontend && npm run dev
```

SQLite and local disk are the defaults, so a clone runs with no database or
object store to set up.

### Optional native tools

| Tool | Enables | Without it |
|---|---|---|
| Tesseract + `pytesseract` | OCR | 503 with install instructions |
| LibreOffice | Word/Excel/PowerPoint **to** PDF | 503 with install instructions |

```bash
scoop install tesseract        # or: winget install UB-Mannheim.TesseractOCR
pip install pytesseract
```

Tesseract ships with no language data. Put the `.traineddata` files you need
in its `tessdata` directory — `eng`, `deu`, `fra`, `spa` from
[tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) cover most
cases.

---

## Configuration

Every setting is read from the environment with a `DOCINTEL_` prefix.

| Variable | Default | Notes |
|---|---|---|
| `DOCINTEL_ENVIRONMENT` | `development` | `development` · `test` · `production` |
| `DOCINTEL_AUTH_MODE` | `required` | `open` refuses to boot in production |
| `DOCINTEL_SECRET_KEY` | — | Required in production |
| `DOCINTEL_ACCESS_TOKEN_TTL_MINUTES` | `60` | |
| `DOCINTEL_DATABASE_URL` | SQLite file | Any SQLAlchemy URL |
| `DOCINTEL_STORAGE_DRIVER` | `local` | `local` · `s3` |
| `DOCINTEL_STORAGE_ROOT` | `.storage` | Local driver only |
| `DOCINTEL_S3_BUCKET` / `_REGION` / `_ENDPOINT_URL` | — | Credentials come from boto3's chain, never from config |
| `DOCINTEL_MAX_UPLOAD_MB` | `200` | |
| `DOCINTEL_WORKER_POLL_SECONDS` | `1.0` | |
| `DOCINTEL_JOB_MAX_ATTEMPTS` | `3` | |
| `DOCINTEL_JOB_TIMEOUT_SECONDS` | `900` | |
| `OPENROUTER_API_KEY` | — | AI features only |
| `DOCINTEL_AI_WORKERS` | `4` | Parallel chunk summarisation |

---

## Deployment

Implemented and tested, switched off by default:

```bash
pip install -r requirements-deploy.txt
```

**Postgres** — set `DOCINTEL_DATABASE_URL` to a `postgresql+psycopg://` URL.
The schema and every query are engine-agnostic, and job claiming uses a
conditional `UPDATE` rather than anything SQLite-specific, so the migrations
apply unchanged.

**S3 or S3-compatible** — set `DOCINTEL_STORAGE_DRIVER=s3` and
`DOCINTEL_S3_BUCKET`, plus `DOCINTEL_S3_ENDPOINT_URL` for MinIO, R2 or B2.
Credentials resolve through boto3's chain, so a deployment on EC2 or ECS
stores no secrets.

---

## Testing

```bash
.venv\Scripts\python -m pytest tests/ test_app.py -q    # 657 backend
cd frontend && npm test                                 # 73 frontend
```

Tests open their outputs with the library that owns the format — python-docx
reads the DOCX, openpyxl the XLSX, python-pptx the deck, Pillow the images,
pypdf the PDFs. Producing bytes is not passing.

The PDF corpus in `tests/pdf_corpus.py` builds real files carrying real
constructs: embedded JavaScript, launch actions, executable attachments, XFA
forms, corrupt cross-reference tables.

Some tests exist because a regression would not produce a red test.
`test_render_concurrency.py` covers PDFium being driven from several threads:
unsynchronised, it does not raise, it segfaults and takes the server down.

---

## Dependencies and licensing

All permissive and safe for commercial distribution.

| Library | Licence | Used for |
|---|---|---|
| pypdf | BSD-3-Clause | page operations, forms, composition |
| pikepdf | MPL-2.0 | AES-256 encryption, compression, content streams (QPDF) |
| pypdfium2 | BSD-3 / Apache-2.0 | rasterisation (PDFium) |
| pdfplumber | MIT | table detection |
| reportlab | BSD | overlays and generated documents |
| python-docx, openpyxl, python-pptx | MIT | Office output |
| Pillow | MIT-CMU | image handling |
| numpy | BSD-3 | scan analysis |
| cryptography | Apache-2.0 / BSD-3 | reading AES-protected PDFs |
| FastAPI, SQLAlchemy, Alembic | MIT | platform |
| React, PDF.js | MIT / Apache-2.0 | frontend |

**PyMuPDF is deliberately not used.** It is the obvious choice for much of
this and it is AGPL-3.0, which would oblige you to publish the source of a
networked application that links it, or buy a commercial licence. Everything
it would have provided is covered above.

---

## Things this project is deliberately honest about

These are design decisions, not oversights.

**Nothing overwrites your document.** Every operation appends a version. The
original is always downloadable, and restore is additive so it can itself be
undone.

**Destructive claims are verified.** Redaction deletes text from the content
stream, then re-parses the output; if any redacted string is still extractable
the operation fails and discards the result rather than returning a document
that only looks redacted. OCR and text editing verify the same way.

**Conversion fidelity is stated, not implied.** A PDF does not record
paragraphs, headings or table structure. Every target declares `exact`,
`structural`, `text-only` or `raster`, and that travels with the output.

**A clean security scan is not a safety guarantee.** The scanner reports "no
suspicious indicators were detected by the available checks" in those words.
Static structural checks cannot prove a file is safe.

**Citations are verified.** Answers are asked for with `[p.N]` markers and
every marker is checked against the pages actually retrieved. A citation to a
page that was never in context is stripped and reported.

**Quotes are verified.** Anything the model paraphrased rather than copied
verbatim is discarded.

**Analysis separates fact from inference.** What the document states and what
the model concluded are returned, and displayed, separately.

**Replacement text cannot always match.** Embedded fonts are usually
subsetted, so new glyphs cannot be added to them. Edited text is drawn in a
standard font matched for size, colour and position, and the API names the
font it used. PDF text does not reflow, so a longer replacement is scaled to
fit and warned about when it cannot be.

**Signing is visible, not cryptographic.** It applies a signature image and
records a tamper-evident audit trail with the document hash frozen at send
time. It is not PAdES. Whether that satisfies a jurisdiction is a legal
question this product does not answer.

**Despeckling has a limit.** It tells dirt from text by the ink surrounding
each dark pixel. Where print is so fine that its strokes are as thin as the
specks, the two are not distinguishable and some of that text will thin.
Scanning at a higher resolution is the fix.

**Unavailable features say why.** OCR needs Tesseract; office-to-PDF needs
LibreOffice. Without them the API returns 503 with install instructions rather
than empty results that look like success.

**The viewer does not need the server.** Rendering, text selection, thumbnails
and search run in the browser on a document this tab already holds, so they
keep working if the API goes away. Anything that writes does not, and the app
says which is which rather than letting a button fail with an error that reads
like a broken feature.

**Documents are untrusted input to the AI.** Every piece of extracted text is
fenced in unguessable delimiters before it reaches a model, and the system
prompt states it is data, never instructions.

---

## Not built

**Cryptographic (PAdES) signatures.** See above.

**Image editing inside the PDF.** It needs a full object model of the page,
and the result is rarely better than replacing the image.

**PDF/A conversion and preflight.** Both need Ghostscript — another
dependency and another licence to clear.

**Accessibility tagging.** Large, and done badly it is worse than nothing: a
mis-tagged document is harder to use with a screen reader than an untagged
one.

**An `rq`/Redis queue, deliberately.** Job state, progress and retries live in
the jobs table because the API reads them there. An external broker would
still need those rows, leaving two systems tracking one job. The database
queue claims work with a conditional `UPDATE`, which is safe across processes
without a broker.

`app.py` is the original single-user Streamlit prototype, kept for reference
and deployable on its own. It is not part of the platform.

# DocIntel

A document intelligence platform: read, analyse, edit, convert, redact, sign
and translate PDFs.

FastAPI backend, React + PDF.js frontend, background worker. 508 tests.

---

## Running it

Three processes. Python 3.13, Node 20+.

```bash
# 1. install
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        # Windows
cd frontend && npm install && cd ..

# 2. configure
copy .env.example .env                               # then add your key

# 3. migrate
.venv\Scripts\python -m alembic upgrade head
```

```bash
# terminal 1 — API            http://127.0.0.1:8000   (docs at /docs)
.venv\Scripts\python -m uvicorn docintel.main:app --port 8000

# terminal 2 — worker
.venv\Scripts\python -m docintel.jobs.worker

# terminal 3 — frontend       http://localhost:5173
cd frontend && npm run dev
```

SQLite and local disk are the defaults, so a clone runs with no database or
object store to set up. Point `DOCINTEL_DATABASE_URL` at Postgres and
`DOCINTEL_STORAGE_DRIVER` at S3 for a real deployment.

---

## What it does

**Read** — PDF.js viewer with a real text layer, thumbnails, search with
highlight rectangles, snapshot any region to an image.

**Analyse** — whole-document summaries in four styles, structured analysis,
key quotes, keywords, sentiment and readability. Ask questions and get answers
with page citations.

**Edit** — rotate, reorder by drag, delete, duplicate, extract, crop, split,
merge, watermark, page numbers, headers and footers, compress.

**Forms** — detect and fill existing forms; build new fillable ones by
dragging fields onto the page.

**Sign** — Fill & Sign for yourself, or send a multi-party signature request
with sequential order, decline handling and an audit trail.

**Secure** — static security scanner, true redaction, AES-256 protection,
authorised unlock.

**Convert** — PDF to text, Markdown, HTML, JSON, CSV, XLSX, DOCX and images;
text, Markdown, CSV and images to PDF.

**Compare** — page-aligned diff with changed numbers and dates extracted.

**Translate** — whole document or selected pages, with a shared glossary so
terminology stays consistent.

---

## Things this project is deliberately honest about

These are design decisions, not oversights.

**Nothing overwrites your document.** Every operation appends a version. The
original is always downloadable, and restore is additive so it can itself be
undone.

**Conversion fidelity is stated, not implied.** A PDF does not record
paragraphs, headings or table structure. Every conversion target declares
`exact`, `structural`, `text-only` or `raster`, and that travels with the
output.

**A clean security scan is not a safety guarantee.** The scanner reports "no
suspicious indicators were detected by the available checks" and says so in
those words. Static structural checks cannot prove a file is safe.

**Redaction removes content, and proves it.** Text is deleted from the page's
content stream, then the output is re-parsed. If any redacted string is still
extractable the operation fails and discards the result rather than returning
a document that only looks redacted.

**Citations are verified.** Answers are asked for with `[p.N]` markers and
every marker is checked against the pages actually retrieved. A citation to a
page that was never in context is stripped and reported.

**Quotes are verified.** Anything the model paraphrased rather than copied
verbatim is discarded.

**Analysis separates fact from inference.** What the document states and what
the model concluded are returned — and displayed — in separate sections.

**Signing is visible, not cryptographic.** It applies a signature image and
records a tamper-evident audit trail with the document hash frozen at send
time. It is not PAdES. Whether that satisfies a jurisdiction is a legal
question the product does not answer.

**Unavailable features say why.** OCR needs Tesseract; office-to-PDF needs
LibreOffice. Without them the API returns 503 with install instructions rather
than empty results that look like success.

**Documents are untrusted input to the AI.** Every piece of extracted text is
fenced in unguessable delimiters before it reaches a model, and the system
prompt states it is data, never instructions.

---

## Security

- Bearer-token auth, bcrypt, JWT with a pinned algorithm.
- Object-level authorization on every document route, joining through
  workspace membership. Cross-tenant reads return **404**, not 403, so ids
  cannot be enumerated.
- Uploads validated by magic bytes, not filename or declared type.
- Signature assets are per-user and never served from a public URL.
- Audit logs record identifiers and outcomes, never document content.

`DOCINTEL_AUTH_MODE=open` disables **authentication** for local development.
Authorization is unaffected — the dev user is a real user in a real workspace,
so tenant isolation still applies and is tested in that mode. The server
**refuses to start** with it in production.

---

## Testing

```bash
.venv\Scripts\python -m pytest tests/ test_app.py -q    # 488 backend
cd frontend && npm test                                 # 20 frontend
```

Tests open their outputs with the library that owns the format — python-docx
reads the DOCX, openpyxl reads the XLSX, Pillow reads the images, pypdf reads
the PDFs. Producing bytes is not passing. The PDF corpus in
`tests/pdf_corpus.py` builds real files carrying real constructs: embedded
JavaScript, launch actions, executable attachments, XFA forms, corrupt xrefs.

---

## Dependencies and licensing

All permissive and safe for commercial distribution:

| Library | Licence | Used for |
|---|---|---|
| pypdf | BSD-3-Clause | page operations, forms, composition |
| pikepdf | MPL-2.0 | AES-256 encryption, compression (QPDF) |
| pypdfium2 | BSD-3 / Apache-2.0 | rasterisation (PDFium) |
| pdfplumber | MIT | table detection |
| reportlab | BSD | overlays, generated documents |
| python-docx, openpyxl | MIT | DOCX and XLSX output |
| Pillow | MIT-CMU | image handling |
| cryptography | Apache-2.0 / BSD-3 | reading AES-protected PDFs |

**PyMuPDF is deliberately not used.** It is the obvious choice for much of
this and it is AGPL-3.0, which would oblige you to publish the source of a
networked application that links it, or buy a commercial licence. Everything
it would have provided is covered above.

---

## Not built

Autosave indicator, mobile/touch polish beyond responsive CSS, and
cryptographic (PAdES) signatures.

`app.py` is the original single-user Streamlit prototype, kept for reference.
It is not part of the platform.

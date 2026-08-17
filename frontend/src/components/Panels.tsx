/**
 * Right-hand side panels: comments, search, security, form, redaction.
 * Each is a thin view over an API call — no business logic lives here.
 */
import { useEffect, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  api, type Annotation, type FormReport, type RedactCandidate,
  type SearchMatch, type SecurityReport,
} from "../api";
import { searchDocument } from "../localSearch";
import {
  clearDraft, readDraft, useAutosaveDraft, type DraftStatus,
} from "../useDraft";

// ------------------------------------------------------------- comments

export function CommentsPanel({
  annotations, onJump, onResolve, onReopen, onDelete,
}: {
  annotations: Annotation[];
  onJump: (a: Annotation) => void;
  onResolve: (a: Annotation) => void;
  onReopen: (a: Annotation) => void;
  onDelete: (a: Annotation) => void;
}) {
  const threads = annotations.filter((a) => !a.parent_id);
  const repliesOf = (id: string) => annotations.filter((a) => a.parent_id === id);

  if (!annotations.length) {
    return (
      <div className="empty">
        <h4>No annotations yet</h4>
        <p className="small">Select text in the document to highlight or comment.</p>
      </div>
    );
  }

  return (
    <>
      {threads.map((thread) => (
        <div key={thread.id} className={`item ${thread.is_resolved ? "resolved" : ""}`}>
          <div className="meta spread">
            <span>Page {thread.page} · {thread.kind}</span>
            <span className={`badge ${thread.is_resolved ? "ok" : "info"}`}>
              {thread.is_resolved ? "resolved" : "open"}
            </span>
          </div>

          {thread.selected_text && (
            <div className="quote" onClick={() => onJump(thread)}>
              {thread.selected_text.slice(0, 180)}
            </div>
          )}
          {thread.body && <div className="text">{thread.body}</div>}

          {repliesOf(thread.id).map((reply) => (
            <div key={reply.id} className="text" style={{ marginTop: 6, paddingLeft: 10,
              borderLeft: "2px solid var(--line)" }}>
              {reply.body}
            </div>
          ))}

          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn sm ghost" onClick={() => onJump(thread)}>Go to</button>
            {thread.is_resolved ? (
              <button className="btn sm ghost" onClick={() => onReopen(thread)}>Reopen</button>
            ) : (
              <button className="btn sm ghost" onClick={() => onResolve(thread)}>Resolve</button>
            )}
            <button className="btn sm ghost" style={{ color: "var(--bad)" }}
              onClick={() => onDelete(thread)}>Delete</button>
          </div>
        </div>
      ))}
    </>
  );
}

// --------------------------------------------------------------- search

export function SearchPanel({
  documentId, pdf, onJump,
}: {
  documentId: string;
  /** The loaded document, when this tab has one. */
  pdf?: PDFDocumentProxy | null;
  onJump: (match: SearchMatch, index: number, all: SearchMatch[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [wholeWords, setWholeWords] = useState(false);
  const [matches, setMatches] = useState<SearchMatch[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [source, setSource] = useState<string | null>(null);

  async function run(event?: React.FormEvent) {
    event?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError("");
    try {
      // Local first. The text and its geometry are already in this tab, so
      // searching here answers immediately and keeps working if the API is
      // unreachable. The server is the fallback: it reads the stored bytes,
      // so it can search a document this tab has not finished loading.
      if (pdf) {
        const found = await searchDocument(pdf, query, { wholeWords });
        setMatches(found as unknown as SearchMatch[]);
        setSource("this browser");
      } else {
        const result = await api.search(documentId, query, wholeWords);
        setMatches(result.matches);
        setSource("the server");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form onSubmit={run} className="stack" style={{ marginBottom: "0.8rem" }}>
        <input
          className="input"
          placeholder="Find in document…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="spread">
          <label className="row small muted" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={wholeWords}
              onChange={(e) => setWholeWords(e.target.checked)} />
            Whole words
          </label>
          <button className="btn sm primary" disabled={busy || !query.trim()}>
            {busy ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      {error && <div className="error">{error}</div>}

      {matches && (
        <div className="small muted" style={{ marginBottom: 8 }}>
          {matches.length} match{matches.length === 1 ? "" : "es"}
          {source ? ` · searched in ${source}` : ""}
        </div>
      )}

      {matches?.map((match, index) => (
        <div key={`${match.page}-${match.start}`} className="item"
          onClick={() => onJump(match, index, matches)}>
          <div className="meta">Page {match.page}</div>
          <div className="text small">{match.context}</div>
        </div>
      ))}
    </>
  );
}

// ------------------------------------------------------------- security

export function SecurityPanel({ documentId }: { documentId: string }) {
  const [report, setReport] = useState<SecurityReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.security(documentId).then(setReport).catch((e) => setError(e.message));
  }, [documentId]);

  if (error) return <div className="error">{error}</div>;
  if (!report) return <div className="row"><span className="spinner" /> Loading…</div>;

  if (!report.scanned) {
    return (
      <div className="empty">
        <h4>Scan pending</h4>
        <p className="small">The security scan runs in the background after upload.</p>
      </div>
    );
  }

  const tone =
    report.risk_level === "high" ? "bad"
      : report.risk_level === "medium" ? "warn"
        : report.risk_level === "none" ? "ok" : "info";

  return (
    <>
      <div className="card" style={{ padding: "0.8rem", marginBottom: "0.8rem" }}>
        <div className="spread" style={{ marginBottom: 6 }}>
          <strong className="small">Risk</strong>
          <span className={`badge ${tone}`}>{report.risk_label}</span>
        </div>
        <p className="small muted" style={{ margin: 0, lineHeight: 1.5 }}>{report.headline}</p>
      </div>

      {report.findings.map((finding) => (
        <div key={finding.finding_id} className={`finding ${finding.severity}`}>
          <div className="spread">
            <h5>{finding.title}</h5>
            <span className={`badge ${
              finding.severity === "high" ? "bad"
                : finding.severity === "medium" ? "warn" : "info"}`}>
              {finding.severity}
            </span>
          </div>
          <p>{finding.detail}</p>
          {finding.locations && (
            <p className="small muted" style={{ marginTop: 4 }}>{finding.locations}</p>
          )}
        </div>
      ))}
    </>
  );
}

// ----------------------------------------------------------------- form

export function FormPanel({
  documentId, onFilled,
}: {
  documentId: string;
  onFilled: (message: string) => void;
}) {
  const [report, setReport] = useState<FormReport | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [recovered, setRecovered] = useState(false);

  const draft = useAutosaveDraft(
    "form", documentId, values,
    (v) => Object.values(v).every((entry) => !entry),
  );

  async function load() {
    try {
      const result = await api.form(documentId);
      setReport(result);

      const onDocument = Object.fromEntries(
        result.fields.filter((f) => f.value).map((f) => [f.name, f.value as string]),
      );

      // A draft is unsubmitted typing, so it wins over the values already in
      // the file — but only for fields the form still has. A field removed
      // since the draft was written must not be resurrected into the payload.
      const saved = readDraft<Record<string, string>>("form", documentId);
      if (saved) {
        const names = new Set(result.fields.map((f) => f.name));
        const usable = Object.fromEntries(
          Object.entries(saved).filter(([name, value]) => names.has(name) && value),
        );
        if (Object.keys(usable).length) {
          setValues({ ...onDocument, ...usable });
          setRecovered(true);
          return;
        }
      }
      setValues(onDocument);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => { load(); }, [documentId]);

  if (error) return <div className="error">{error}</div>;
  if (!report) return <div className="row"><span className="spinner" /> Loading…</div>;

  if (!report.has_form || !report.fillable) {
    return (
      <div className="empty">
        <h4>{report.has_form ? "Form not fillable" : "No form fields"}</h4>
        <p className="small">{report.note}</p>
      </div>
    );
  }

  async function submit(flatten: boolean) {
    setBusy(true);
    setError("");
    try {
      const result = await api.fillForm(documentId, values, flatten);
      // The values are on the document now, so the local draft has served its
      // purpose. Clearing it before reloading stops load() offering it back.
      clearDraft("form", documentId);
      setRecovered(false);
      onFilled(result.note || `Saved as version ${result.version}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="small muted" style={{ marginBottom: 10 }}>{report.note}</div>

      {recovered && (
        <div className="notice" style={{ marginBottom: 10 }}>
          <span className="small">Unsubmitted entries from this browser were restored.</span>
          <button
            className="btn sm ghost"
            onClick={() => {
              draft.discard();
              setRecovered(false);
              load();
            }}
          >
            Discard draft
          </button>
        </div>
      )}

      {report.fields.map((field) => (
        <label key={field.name} className="field">
          <span>
            {field.name}
            {field.required && <em style={{ color: "var(--bad)" }}> *</em>}
            {field.read_only && " (read-only)"}
          </span>

          {field.kind === "dropdown" || field.kind === "listbox" ? (
            <select
              className="input"
              disabled={field.read_only}
              value={values[field.name] ?? ""}
              onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
            >
              <option value="">—</option>
              {field.options.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          ) : field.kind === "checkbox" ? (
            <input
              type="checkbox"
              disabled={field.read_only}
              checked={(values[field.name] ?? "/Off") !== "/Off"}
              onChange={(e) =>
                setValues({ ...values, [field.name]: e.target.checked ? "/Yes" : "/Off" })}
            />
          ) : field.kind === "multiline" ? (
            <textarea
              className="input"
              rows={3}
              disabled={field.read_only}
              value={values[field.name] ?? ""}
              onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
            />
          ) : (
            <input
              className="input"
              disabled={field.read_only}
              title={field.tooltip ?? undefined}
              maxLength={field.max_length ?? undefined}
              value={values[field.name] ?? ""}
              onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
            />
          )}
        </label>
      ))}

      <div className="row">
        <button className="btn primary" disabled={busy} onClick={() => submit(false)}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button className="btn" disabled={busy} onClick={() => submit(true)}
          title="Write values in and remove the interactive layer">
          Save flattened
        </button>
        <div style={{ flex: 1 }} />
        <DraftIndicator status={draft.status} />
      </div>

      <p className="small muted" style={{ marginBottom: 0 }}>
        Entries are kept in this browser as you type. They are only written to
        the document when you save.
      </p>
    </>
  );
}

/** Shared "kept locally" indicator. Renders nothing when there is no news. */
export function DraftIndicator({ status }: { status: DraftStatus }) {
  if (status === "clean") return null;
  return status === "saving"
    ? <span className="small muted">Keeping draft…</span>
    : <span className="small" style={{ color: "var(--ok)" }}>Draft kept</span>;
}

// ------------------------------------------------------------ redaction

export function RedactPanel({
  documentId, onPreview, onApplied,
}: {
  documentId: string;
  onPreview: (candidates: RedactCandidate[]) => void;
  onApplied: (message: string) => void;
}) {
  const [candidates, setCandidates] = useState<RedactCandidate[] | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  async function scan() {
    setBusy(true);
    setError("");
    try {
      const result = await api.detectSensitive(documentId);
      setCandidates(result.candidates);
      setChosen(new Set(result.candidates.map((_, i) => i)));
      onPreview(result.candidates);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggle(index: number) {
    const next = new Set(chosen);
    next.has(index) ? next.delete(index) : next.add(index);
    setChosen(next);
    onPreview((candidates ?? []).filter((_, i) => next.has(i)));
  }

  async function apply() {
    if (!candidates) return;
    setBusy(true);
    setError("");
    try {
      const targets = candidates.filter((_, i) => chosen.has(i));
      const result = await api.applyRedaction(documentId, targets);
      onApplied(result.note);
      setCandidates(null);
      setConfirming(false);
      onPreview([]);
    } catch (e) {
      setError((e as Error).message);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>
        Detection changes nothing. Review the matches, then apply — content is
        removed from the file, not just covered over.
      </div>

      <button className="btn primary" disabled={busy} onClick={scan}
        style={{ marginBottom: "0.8rem" }}>
        {busy ? "Scanning…" : "Find sensitive information"}
      </button>

      {error && <div className="error">{error}</div>}

      {candidates && candidates.length === 0 && (
        <div className="empty"><h4>Nothing detected</h4></div>
      )}

      {candidates?.map((candidate, index) => (
        <label key={`${candidate.page}-${candidate.start}-${index}`} className="item"
          style={{ display: "block", cursor: "pointer" }}>
          <div className="row">
            <input type="checkbox" checked={chosen.has(index)}
              onChange={() => toggle(index)} />
            <span className="badge info">{candidate.kind}</span>
            <span className="small muted">p{candidate.page}</span>
          </div>
          <div className="text" style={{ marginTop: 4, wordBreak: "break-all" }}>
            {candidate.text}
          </div>
        </label>
      ))}

      {candidates && candidates.length > 0 && (
        confirming ? (
          <div className="card" style={{ padding: "0.8rem", marginTop: "0.6rem" }}>
            <p className="small" style={{ marginTop: 0 }}>
              Permanently remove {chosen.size} item(s)? The current version keeps
              the original text and stays downloadable.
            </p>
            <div className="row">
              <button className="btn danger" disabled={busy} onClick={apply}>
                {busy ? "Redacting…" : "Yes, redact"}
              </button>
              <button className="btn ghost" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <button className="btn danger" disabled={busy || chosen.size === 0}
            onClick={() => setConfirming(true)} style={{ marginTop: "0.6rem" }}>
            Redact {chosen.size} selected
          </button>
        )
      )}
    </>
  );
}

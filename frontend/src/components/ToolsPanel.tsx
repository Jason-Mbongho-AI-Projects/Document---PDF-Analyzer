/**
 * Translate and OCR panels.
 *
 * Both surface the server's honest position: translation produces a new
 * text-only document rather than the original with words swapped, and OCR
 * says what it needs when no engine is installed instead of silently
 * returning nothing.
 */
import { useEffect, useState } from "react";
import { api, type OcrAssessment, type TranslationResult } from "../api";

const LANGUAGES = [
  "French", "German", "Spanish", "Italian", "Portuguese", "Dutch",
  "Polish", "Arabic", "Hindi", "Japanese", "Korean",
  "Simplified Chinese", "Traditional Chinese",
];

export function TranslatePanel({
  documentId, pageCount, onSaved,
}: {
  documentId: string;
  pageCount: number;
  onSaved: (message: string) => void;
}) {
  const [language, setLanguage] = useState("French");
  const [scope, setScope] = useState<"all" | "pages">("all");
  const [pageSpec, setPageSpec] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TranslationResult | null>(null);
  const [error, setError] = useState("");

  function parsePages(): number[] | undefined {
    if (scope === "all") return undefined;
    const wanted = new Set<number>();
    for (const part of pageSpec.split(",")) {
      const range = part.trim().split("-").map((n) => parseInt(n, 10));
      if (range.length === 2 && !range.some(isNaN)) {
        for (let p = range[0]; p <= range[1]; p++) wanted.add(p);
      } else if (!isNaN(range[0])) {
        wanted.add(range[0]);
      }
    }
    return [...wanted].filter((p) => p >= 1 && p <= pageCount).sort((a, b) => a - b);
  }

  async function run() {
    const pages = parsePages();
    if (scope === "pages" && (!pages || pages.length === 0)) {
      setError("No valid pages in that range.");
      return;
    }

    setBusy(true);
    setError("");
    setResult(null);
    try {
      const translated = await api.translate(documentId, language, pages);
      setResult(translated);
      if (translated.version) {
        onSaved(`Translation saved as version ${translated.version}`);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="field">
        <span>Target language</span>
        <select className="input" value={language}
          onChange={(e) => setLanguage(e.target.value)}>
          {LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </label>

      <label className="field">
        <span>Scope</span>
        <select className="input" value={scope}
          onChange={(e) => setScope(e.target.value as "all" | "pages")}>
          <option value="all">Whole document ({pageCount} pages)</option>
          <option value="pages">Selected pages</option>
        </select>
      </label>

      {scope === "pages" && (
        <label className="field">
          <span>Pages (e.g. 1-3, 7)</span>
          <input className="input" value={pageSpec}
            onChange={(e) => setPageSpec(e.target.value)} />
        </label>
      )}

      <button className="btn primary" disabled={busy} onClick={run}>
        {busy ? "Translating…" : "Translate"}
      </button>

      {busy && (
        <div className="small muted" style={{ marginTop: 8 }}>
          Each page is a separate model call, so a long document takes a while.
        </div>
      )}

      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}

      {result && (
        <div style={{ marginTop: "1rem" }}>
          <div className="notice" style={{ marginBottom: "0.7rem" }}>{result.note}</div>

          {Object.keys(result.glossary).length > 0 && (
            <>
              <div className="meta">Glossary used for consistency</div>
              {Object.entries(result.glossary).slice(0, 12).map(([from, to]) => (
                <div key={from} className="small muted">{from} → {to}</div>
              ))}
            </>
          )}

          <div className="meta" style={{ marginTop: 10 }}>Translated pages</div>
          {result.pages.map((page) => (
            <div key={page.page} className="item" style={{ cursor: "default" }}>
              <div className="meta">Page {page.page}</div>
              <div className="text" style={{ whiteSpace: "pre-wrap" }}>
                {page.translated.slice(0, 600)}
              </div>
            </div>
          ))}

          <div className="small muted" style={{ marginTop: 8 }}>
            {result.tokens} tokens
          </div>
        </div>
      )}
    </>
  );
}

export function OcrPanel({
  documentId, notify,
}: {
  documentId: string;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [assessment, setAssessment] = useState<OcrAssessment | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.ocrAssess(documentId)
      .then(setAssessment)
      .catch((e) => setError((e as Error).message));
  }, [documentId]);

  async function run() {
    setBusy(true);
    setError("");
    try {
      const result = await api.runOcr(documentId);
      setText(result.pages.map((p) => `--- Page ${p.page} ---\n${p.text}`).join("\n\n"));
      notify(`OCR complete (${result.engine})`, "ok");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !assessment) return <div className="error">{error}</div>;
  if (!assessment) return <div className="row"><span className="spinner" /> Checking…</div>;

  const tone =
    assessment.classification === "native" ? "ok"
      : assessment.classification === "mixed" ? "warn" : "bad";

  return (
    <>
      <div className="card" style={{ padding: "0.8rem", marginBottom: "0.8rem" }}>
        <div className="spread" style={{ marginBottom: 6 }}>
          <strong className="small">Text layer</strong>
          <span className={`badge ${tone}`}>
            {assessment.classification.replace("_", " ")}
          </span>
        </div>
        <p className="small muted" style={{ margin: 0, lineHeight: 1.5 }}>
          {assessment.summary}
        </p>
      </div>

      {!assessment.engine.available ? (
        <div className="notice">
          <strong>OCR engine not configured.</strong>
          <div style={{ marginTop: 6 }}>{assessment.engine.reason}</div>
          <div style={{ marginTop: 6 }}>
            Detecting which pages need OCR works without an engine — that is the
            result shown above.
          </div>
        </div>
      ) : (
        <>
          <button className="btn primary" disabled={busy} onClick={run}>
            {busy ? "Reading…" : `Run OCR (${assessment.engine.name})`}
          </button>
          {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}
        </>
      )}

      {assessment.pages_needing_ocr.length > 0 && (
        <div className="small muted" style={{ marginTop: 10 }}>
          Pages without text: {assessment.pages_needing_ocr.join(", ")}
        </div>
      )}

      {text && (
        <div className="card" style={{ padding: "0.75rem", marginTop: "0.8rem" }}>
          <div className="text" style={{ whiteSpace: "pre-wrap", fontSize: ".78rem" }}>
            {text.slice(0, 4000)}
          </div>
        </div>
      )}
    </>
  );
}

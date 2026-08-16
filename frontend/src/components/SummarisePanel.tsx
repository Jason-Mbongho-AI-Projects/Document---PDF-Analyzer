/**
 * Summarise and Analyse — the product's original core, in the workspace.
 *
 * The analysis view keeps two things visually apart: what the document
 * states, and what the model inferred from it. They are different kinds of
 * claim, and a reader deciding whether to trust a finding needs to know which
 * one they are looking at.
 */
import { useEffect, useState } from "react";
import {
  api, type AnalysisResult, type InsightsResult, type QuotesResult,
  type SummaryResult,
} from "../api";

const MODES: [string, string][] = [
  ["brief", "Brief"],
  ["detailed", "Detailed"],
  ["bullet_points", "Bullets"],
  ["executive", "Executive"],
];

export function SummarisePanel({
  documentId, onJumpToPage, notify,
}: {
  documentId: string;
  onJumpToPage: (page: number) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [mode, setMode] = useState("detailed");
  const [summary, setSummary] = useState<SummaryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showSections, setShowSections] = useState(false);
  const [quotes, setQuotes] = useState<QuotesResult | null>(null);
  const [quotesBusy, setQuotesBusy] = useState(false);

  async function run(refresh = false) {
    setBusy(true);
    setError("");
    try {
      setSummary(await api.summarize(documentId, mode, refresh));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download(format: "txt" | "csv") {
    try {
      const blob = await api.downloadSummary(documentId, mode, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `summary.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  return (
    <>
      <div className="meta">Summary type</div>
      <div className="row" style={{ flexWrap: "wrap", marginBottom: "0.8rem" }}>
        {MODES.map(([value, label]) => (
          <button key={value}
            className={`btn sm ${mode === value ? "primary" : ""}`}
            onClick={() => { setMode(value); setSummary(null); }}>
            {label}
          </button>
        ))}
      </div>

      <div className="row" style={{ marginBottom: "0.8rem" }}>
        <button className="btn primary" disabled={busy} onClick={() => run(false)}>
          {busy ? "Summarising…" : "Summarise document"}
        </button>
        {summary && (
          <button className="btn sm" disabled={busy} onClick={() => run(true)}
            title="Ignore the stored summary and generate a fresh one">
            Regenerate
          </button>
        )}
      </div>

      {busy && (
        <div className="small muted" style={{ marginBottom: 8 }}>
          Long documents are split into sections and summarised in parallel,
          then merged.
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {summary && (
        <>
          {summary.injection_detected && (
            <div className="error" style={{ marginBottom: 10 }}>
              {summary.injection_note}
            </div>
          )}

          <div className="card" style={{ padding: "0.85rem", marginBottom: "0.7rem" }}>
            <div className="text" style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
              {summary.summary}
            </div>
          </div>

          <div className="row" style={{ marginBottom: "0.7rem" }}>
            <button className="btn sm" onClick={() => download("txt")}>
              Download .txt
            </button>
            <button className="btn sm" onClick={() => download("csv")}>
              Sections .csv
            </button>
          </div>

          <div className="small muted" style={{ marginBottom: 8 }}>
            {summary.note}
            {summary.cached && " · reused a stored summary"}
            {summary.tokens > 0 && ` · ${summary.tokens} tokens`}
          </div>

          <button className="btn sm ghost"
            onClick={() => setShowSections(!showSections)}>
            {showSections ? "Hide" : "Show"} {summary.sections.length} section
            summar{summary.sections.length === 1 ? "y" : "ies"}
          </button>

          {showSections && summary.sections.map((section) => (
            <div key={section.index} className="item" style={{ cursor: "default" }}>
              <div className="meta">
                Section {section.index}
                {section.pages && ` · ${section.pages}`}
                {section.failed && " · failed"}
              </div>
              <div className="text" style={{ whiteSpace: "pre-wrap" }}>
                {section.summary}
              </div>
            </div>
          ))}
        </>
      )}

      <div className="meta" style={{ marginTop: 16 }}>Key quotes</div>
      <button className="btn sm" disabled={quotesBusy} onClick={async () => {
        setQuotesBusy(true);
        try {
          setQuotes(await api.quotes(documentId));
        } catch (e) {
          notify((e as Error).message, "error");
        } finally {
          setQuotesBusy(false);
        }
      }}>
        {quotesBusy ? "Extracting…" : "Extract key quotes"}
      </button>

      {quotes && (
        <>
          {quotes.quotes.length === 0 && (
            <div className="small muted" style={{ marginTop: 8 }}>
              No passages could be verified against the document text.
            </div>
          )}
          {quotes.quotes.map((quote, index) => (
            <div key={index} className="item"
              onClick={() => quote.page && onJumpToPage(quote.page)}>
              <div className="meta">
                {quote.page ? `Page ${quote.page} — click to open` : "Quote"}
              </div>
              <div className="quote">{quote.text}</div>
            </div>
          ))}
          <div className="small muted" style={{ marginTop: 6 }}>{quotes.note}</div>
        </>
      )}
    </>
  );
}

export function AnalysePanel({
  documentId, onJumpToPage,
}: {
  documentId: string;
  onJumpToPage: (page: number) => void;
}) {
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [insights, setInsights] = useState<InsightsResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.insights(documentId).then(setInsights).catch(() => setInsights(null));
  }, [documentId]);

  async function run() {
    setBusy(true);
    setError("");
    try {
      setResult(await api.analyzeDocument(documentId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const page = (n: number | null) =>
    n ? (
      <button className="btn sm ghost" style={{ padding: "0 4px" }}
        onClick={() => onJumpToPage(n)}>p.{n}</button>
    ) : null;

  return (
    <>
      {insights && (
        <div className="card" style={{ padding: "0.8rem", marginBottom: "0.9rem" }}>
          <div className="meta">Document statistics</div>
          <div className="small muted" style={{ marginBottom: 6 }}>
            {insights.page_count} pages · {insights.word_count.toLocaleString()} words
          </div>

          <div className="row" style={{ flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
            {insights.keywords.slice(0, 12).map((word) => (
              <span key={word} className="badge info">{word}</span>
            ))}
          </div>

          <div className="small muted">
            Tone: <strong>{insights.sentiment.sentiment}</strong> ·
            {" "}Reading level: <strong>{insights.readability.reading_level}</strong>
          </div>
          <div className="small muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
            {insights.method_notes.sentiment}
          </div>
        </div>
      )}

      <button className="btn primary" disabled={busy} onClick={run}>
        {busy ? "Analysing…" : "Analyse document"}
      </button>

      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}

      {result && (
        <div style={{ marginTop: "1rem" }}>
          <div className="card" style={{ padding: "0.8rem", marginBottom: "0.8rem" }}>
            <div className="meta">From the document</div>
            <div className="text"><strong>Type:</strong> {result.from_document.document_type || "—"}</div>
            <div className="text"><strong>Purpose:</strong> {result.from_document.purpose || "—"}</div>
            <div className="text"><strong>Audience:</strong> {result.from_document.audience || "—"}</div>
            {result.from_document.topics.length > 0 && (
              <div className="row" style={{ flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                {result.from_document.topics.map((t) => (
                  <span key={t} className="badge info">{t}</span>
                ))}
              </div>
            )}
          </div>

          <Section title="Key points" items={result.from_document.key_points}
            render={(k) => <>{k.point} {page(k.page)}</>} />

          <Section title="Obligations" items={result.from_document.obligations}
            render={(o) => <><strong>{o.who}</strong> — {o.must} {page(o.page)}</>} />

          <Section title="Dates and deadlines" items={result.from_document.dates}
            render={(d) => <><strong>{d.date}</strong> — {d.what} {page(d.page)}</>} />

          <Section title="Risks stated" items={result.from_document.risks}
            render={(r) => <>{r.risk} {page(r.page)}</>} />

          <Section title="Recommendations in the document"
            items={result.from_document.stated_recommendations}
            render={(r) => <>{r.recommendation} {page(r.page)}</>} />

          {Object.entries(result.from_document.entities).some(([, v]) => v.length) && (
            <>
              <div className="meta" style={{ marginTop: 10 }}>Entities</div>
              {Object.entries(result.from_document.entities).map(([kind, values]) =>
                values.length ? (
                  <div key={kind} className="small muted" style={{ marginBottom: 4 }}>
                    <strong>{kind}:</strong> {values.join(", ")}
                  </div>
                ) : null,
              )}
            </>
          )}

          {result.ai_interpretation.observations.length > 0 && (
            <div className="card"
              style={{ padding: "0.8rem", marginTop: "1rem",
                borderLeft: "3px solid var(--warn)" }}>
              <div className="meta" style={{ color: "var(--warn)" }}>
                AI interpretation — not stated in the document
              </div>
              {result.ai_interpretation.observations.map((observation, index) => (
                <div key={index} className="text" style={{ marginTop: 4 }}>
                  {observation}
                </div>
              ))}
            </div>
          )}

          <div className="small muted" style={{ marginTop: 10, lineHeight: 1.5 }}>
            {result.note}
          </div>
        </div>
      )}
    </>
  );
}

function Section<T>({
  title, items, render,
}: {
  title: string;
  items: T[];
  render: (item: T) => React.ReactNode;
}) {
  if (!items?.length) return null;
  return (
    <>
      <div className="meta" style={{ marginTop: 10 }}>{title}</div>
      {items.map((item, index) => (
        <div key={index} className="item" style={{ cursor: "default" }}>
          <div className="text">{render(item)}</div>
        </div>
      ))}
    </>
  );
}

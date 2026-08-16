/**
 * Ask-your-document panel and the result view for selection actions.
 *
 * Citations are rendered as clickable page links, and any citation the server
 * could not verify is shown as a warning rather than hidden — the user should
 * know when the model reached for a page that was not in evidence.
 */
import { useEffect, useState } from "react";
import { api, type AiAnswer, type AiSelectionResult } from "../api";

export function AiPanel({
  documentId, onJumpToPage, pending, onDismissPending,
}: {
  documentId: string;
  onJumpToPage: (page: number) => void;
  pending: AiSelectionResult | null;
  onDismissPending: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<{ available: boolean; reason: string | null } | null>(null);

  useEffect(() => {
    api.aiStatus(documentId)
      .then((s) => setStatus({ available: s.available, reason: s.reason }))
      .catch(() => setStatus({ available: false, reason: "AI status unavailable." }));
  }, [documentId]);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setError("");
    setAnswer(null);
    try {
      setAnswer(await api.aiAsk(documentId, question));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {status && !status.available && (
        <div className="notice" style={{ marginBottom: "0.8rem" }}>
          {status.reason}
        </div>
      )}

      {pending && (
        <div className="card" style={{ padding: "0.8rem", marginBottom: "0.9rem" }}>
          <div className="spread" style={{ marginBottom: 6 }}>
            <span className="badge info">{pending.mode}</span>
            <button className="btn sm ghost" onClick={onDismissPending}>Dismiss</button>
          </div>
          {pending.injection_detected && (
            <div className="error" style={{ marginBottom: 8 }}>
              {pending.injection_note}
            </div>
          )}
          <div className="text" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
            {pending.output}
          </div>
          <div className="small muted" style={{ marginTop: 8 }}>
            {pending.model} · {pending.tokens} tokens
          </div>
        </div>
      )}

      <form onSubmit={ask} className="stack" style={{ marginBottom: "0.8rem" }}>
        <textarea
          className="input"
          rows={3}
          placeholder="Ask a question about this document…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={status ? !status.available : false}
        />
        <button className="btn primary"
          disabled={busy || !question.trim() || (status ? !status.available : false)}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {answer && (
        <div className="card" style={{ padding: "0.85rem" }}>
          <div className="text" style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
            {answer.answer}
          </div>

          {answer.citations.length > 0 && (
            <>
              <div className="meta" style={{ marginTop: 12, marginBottom: 4 }}>
                Sources
              </div>
              {answer.citations.map((citation) => (
                <button
                  key={citation.page}
                  className="item"
                  style={{ display: "block", width: "100%", textAlign: "left",
                    background: "var(--surface-3)" }}
                  onClick={() => onJumpToPage(citation.page)}
                >
                  <div className="meta">Page {citation.page} — click to open</div>
                  <div className="quote">{citation.excerpt}</div>
                </button>
              ))}
            </>
          )}

          {answer.note && (
            <div className={answer.dropped_citations.length ? "error" : "notice"}
              style={{ marginTop: 10 }}>
              {answer.note}
            </div>
          )}

          <div className="small muted" style={{ marginTop: 10 }}>
            Searched page(s) {answer.pages_searched.join(", ") || "—"} ·
            {" "}{answer.retrieval} retrieval · {answer.model} · {answer.tokens} tokens
          </div>
        </div>
      )}
    </>
  );
}

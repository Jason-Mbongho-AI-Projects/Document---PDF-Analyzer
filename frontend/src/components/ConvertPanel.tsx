/**
 * Convert and Compare panels.
 *
 * The convert list shows fidelity next to every target, and unavailable
 * targets stay visible with the reason rather than being hidden — a user who
 * wants PowerPoint should learn why they cannot have it, not wonder where it
 * went.
 */
import { useEffect, useState } from "react";
import {
  api, type ComparisonResult, type ConversionTarget, type DocumentSummary,
} from "../api";

const FIDELITY_TONE: Record<string, string> = {
  exact: "ok",
  structural: "info",
  "text-only": "warn",
  raster: "warn",
};

export function ConvertPanel({
  documentId, notify,
}: {
  documentId: string;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [targets, setTargets] = useState<ConversionTarget[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.conversionCapabilities()
      .then((c) => setTargets(c.from_pdf))
      .catch((e) => notify((e as Error).message, "error"));
  }, [documentId, notify]);

  async function run(target: ConversionTarget) {
    setBusy(target.target);
    try {
      const result = await api.convert(documentId, target.target);

      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      link.click();
      URL.revokeObjectURL(url);

      notify(
        result.warnings
          ? `${result.filename} — ${result.warnings.split(" | ")[0]}`
          : `${result.filename} downloaded`,
        "ok",
      );
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  if (!targets) return <div className="row"><span className="spinner" /> Loading…</div>;

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>
        A PDF does not record paragraphs, headings or table structure. Anything
        that reconstructs them is a best effort — the fidelity of each target is
        shown below.
      </div>

      {targets.map((target) => (
        <div key={target.target} className="item" style={{ cursor: "default" }}>
          <div className="spread">
            <strong className="small">{target.label}</strong>
            <span className={`badge ${FIDELITY_TONE[target.fidelity] ?? "info"}`}>
              {target.fidelity}
            </span>
          </div>
          <div className="small muted" style={{ margin: "4px 0 8px" }}>
            {target.available ? target.fidelity_note : target.reason}
          </div>
          <button
            className="btn sm"
            disabled={!target.available || busy !== null}
            onClick={() => run(target)}
            title={target.available ? undefined : target.reason ?? undefined}
          >
            {busy === target.target ? "Converting…"
              : target.available ? `Download .${target.extension}`
                : "Unavailable"}
          </button>
        </div>
      ))}
    </>
  );
}

export function ComparePanel({
  documentId, workspaceId, notify,
}: {
  documentId: string;
  workspaceId: string;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [against, setAgainst] = useState("");
  const [interpret, setInterpret] = useState(false);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [busy, setBusy] = useState(false);

  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    if (!workspaceId) {
      setLoadError("No workspace is associated with this document.");
      return;
    }
    api.documents(workspaceId)
      .then((r) => {
        setDocuments(r.items.filter((d) => d.id !== documentId));
        setLoadError("");
      })
      // Surfaced, not swallowed: an empty picker with no explanation is
      // indistinguishable from "there is nothing to compare with".
      .catch((e) => setLoadError((e as Error).message));
  }, [workspaceId, documentId]);

  async function run() {
    if (!against) return;
    setBusy(true);
    setResult(null);
    try {
      setResult(await api.compare(documentId, against, interpret));
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="field">
        <span>Compare against</span>
        <select className="input" value={against}
          onChange={(e) => setAgainst(e.target.value)}>
          <option value="">Choose a document…</option>
          {documents.map((doc) => (
            <option key={doc.id} value={doc.id}>{doc.filename}</option>
          ))}
        </select>
      </label>

      <label className="row small muted" style={{ cursor: "pointer", marginBottom: 10 }}>
        <input type="checkbox" checked={interpret}
          onChange={(e) => setInterpret(e.target.checked)} />
        Also explain the changes with AI
      </label>

      <button className="btn primary" disabled={!against || busy} onClick={run}>
        {busy ? "Comparing…" : "Compare"}
      </button>

      {loadError && <div className="error" style={{ marginTop: 10 }}>{loadError}</div>}

      {!loadError && documents.length === 0 && (
        <div className="empty">
          <h4>Nothing to compare with</h4>
          <p className="small">Upload a second document to this workspace.</p>
        </div>
      )}

      {result && (
        <div style={{ marginTop: "1rem" }}>
          <div className="card" style={{ padding: "0.75rem", marginBottom: "0.7rem" }}>
            <strong className="small">{result.summary}</strong>
            <div className="small muted" style={{ marginTop: 4 }}>
              {result.old_page_count} → {result.new_page_count} pages
            </div>
          </div>

          {result.numbers_changed.length > 0 && (
            <>
              <div className="meta">Numbers changed</div>
              {result.numbers_changed.map((change, index) => (
                <div key={index} className="item" style={{ cursor: "default" }}>
                  <div className="text">
                    <s style={{ color: "var(--bad)" }}>{change.old}</s>
                    {" → "}
                    <strong style={{ color: "var(--ok)" }}>{change.new}</strong>
                  </div>
                  <div className="small muted">{change.context}</div>
                </div>
              ))}
            </>
          )}

          {result.dates_changed.length > 0 && (
            <>
              <div className="meta" style={{ marginTop: 8 }}>Dates changed</div>
              {result.dates_changed.map((change, index) => (
                <div key={index} className="item" style={{ cursor: "default" }}>
                  <div className="text">
                    <s style={{ color: "var(--bad)" }}>{change.old}</s>
                    {" → "}
                    <strong style={{ color: "var(--ok)" }}>{change.new}</strong>
                  </div>
                </div>
              ))}
            </>
          )}

          <div className="meta" style={{ marginTop: 10 }}>Pages</div>
          {result.pages.filter((p) => p.status !== "unchanged").map((page, index) => (
            <div key={index} className="item" style={{ cursor: "default" }}>
              <div className="spread">
                <span className="small">
                  {page.old_page ? `p${page.old_page}` : "—"}
                  {" → "}
                  {page.new_page ? `p${page.new_page}` : "—"}
                </span>
                <span className={`badge ${
                  page.status === "added" ? "ok"
                    : page.status === "removed" ? "bad" : "warn"}`}>
                  {page.status}
                </span>
              </div>
              {page.changes.slice(0, 3).map((change, i) => (
                <div key={i} style={{ marginTop: 6 }}>
                  {change.old && (
                    <div className="small" style={{ color: "var(--bad)" }}>
                      − {change.old.slice(0, 160)}
                    </div>
                  )}
                  {change.new && (
                    <div className="small" style={{ color: "var(--ok)" }}>
                      + {change.new.slice(0, 160)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}

          {result.interpretation && (
            <>
              <div className="meta" style={{ marginTop: 10 }}>
                AI interpretation of the diff above
              </div>
              <div className="card" style={{ padding: "0.75rem" }}>
                <div className="text" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
                  {result.interpretation}
                </div>
              </div>
            </>
          )}

          <div className="small muted" style={{ marginTop: 10, lineHeight: 1.5 }}>
            {result.note}
          </div>
        </div>
      )}
    </>
  );
}

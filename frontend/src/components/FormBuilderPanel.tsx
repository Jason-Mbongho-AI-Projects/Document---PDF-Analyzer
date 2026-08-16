/**
 * Form builder and version history panels.
 *
 * Fields are placed by dragging a rectangle on the page, so what you draw is
 * where the field lands. Coordinates come back from the canvas in PDF points
 * with a top-left origin, which is exactly what the builder API expects.
 */
import { useEffect, useState } from "react";
import { api, type Rect } from "../api";

export type DraftField = {
  key: string;
  name: string;
  type: string;
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
  required: boolean;
  options: string;
};

const TYPES = [
  ["text", "Text"],
  ["multiline", "Multi-line text"],
  ["checkbox", "Checkbox"],
  ["dropdown", "Dropdown"],
  ["date", "Date"],
  ["signature", "Signature"],
] as const;

export function FormBuilderPanel({
  documentId, drafts, placing, onStartPlacing, onStopPlacing,
  onRemoveDraft, onClearDrafts, onUpdateDraft, notify, onBuilt,
}: {
  documentId: string;
  drafts: DraftField[];
  placing: string | null;
  onStartPlacing: (type: string) => void;
  onStopPlacing: () => void;
  onRemoveDraft: (key: string) => void;
  onClearDrafts: () => void;
  onUpdateDraft: (key: string, patch: Partial<DraftField>) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
  onBuilt: (message: string) => void;
}) {
  const [existing, setExisting] = useState<{ name: string; kind: string }[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.formBuilderDescribe(documentId)
      .then((d) => setExisting(d.fields.map((f) => ({ name: f.name, kind: f.kind }))))
      .catch(() => setExisting([]));
  }, [documentId]);

  async function build() {
    if (!drafts.length) return;

    const names = drafts.map((d) => d.name.trim());
    if (names.some((n) => !n)) {
      notify("Every field needs a name", "error");
      return;
    }
    if (new Set(names).size !== names.length) {
      notify("Field names must be unique", "error");
      return;
    }

    setBusy(true);
    try {
      const result = await api.buildForm(documentId, drafts.map((d) => ({
        name: d.name.trim(),
        type: d.type,
        page: d.page,
        x: d.x, y: d.y, width: d.width, height: d.height,
        required: d.required,
        options: d.options
          ? d.options.split(",").map((o) => o.trim()).filter(Boolean)
          : [],
      })));
      onClearDrafts();
      onBuilt(result.note ?? `Form saved as version ${result.version}`);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>
        Pick a field type, then drag a rectangle on the page to place it.
        Building writes real fillable fields and verifies the result before
        saving.
      </div>

      <div className="meta">Add a field</div>
      <div className="row" style={{ flexWrap: "wrap", marginBottom: "0.9rem" }}>
        {TYPES.map(([value, label]) => (
          <button
            key={value}
            className={`btn sm ${placing === value ? "primary" : ""}`}
            onClick={() => (placing === value ? onStopPlacing() : onStartPlacing(value))}
          >
            {placing === value ? `Drag on page…` : label}
          </button>
        ))}
      </div>

      {existing.length > 0 && (
        <>
          <div className="meta">Already on this document</div>
          {existing.map((field) => (
            <div key={field.name} className="small muted">
              {field.name} — {field.kind}
            </div>
          ))}
          <div style={{ height: 10 }} />
        </>
      )}

      <div className="meta">New fields ({drafts.length})</div>
      {drafts.length === 0 && (
        <div className="empty">
          <h4>Nothing placed yet</h4>
          <p className="small">Choose a type above and drag on the page.</p>
        </div>
      )}

      {drafts.map((draft) => (
        <div key={draft.key} className="item" style={{ cursor: "default" }}>
          <div className="spread" style={{ marginBottom: 6 }}>
            <span className="badge info">{draft.type}</span>
            <span className="small muted">
              p{draft.page} · {Math.round(draft.width)}×{Math.round(draft.height)}
            </span>
          </div>

          <input
            className="input"
            placeholder="field_name"
            value={draft.name}
            onChange={(e) => onUpdateDraft(draft.key, { name: e.target.value })}
            style={{ marginBottom: 6 }}
          />

          {draft.type === "dropdown" && (
            <input
              className="input"
              placeholder="Option A, Option B"
              value={draft.options}
              onChange={(e) => onUpdateDraft(draft.key, { options: e.target.value })}
              style={{ marginBottom: 6 }}
            />
          )}

          <div className="row">
            <label className="row small muted" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={draft.required}
                onChange={(e) => onUpdateDraft(draft.key, { required: e.target.checked })}
              />
              Required
            </label>
            <button className="btn sm ghost" style={{ color: "var(--bad)" }}
              onClick={() => onRemoveDraft(draft.key)}>Remove</button>
          </div>
        </div>
      ))}

      {drafts.length > 0 && (
        <button className="btn primary" disabled={busy} onClick={build}
          style={{ marginTop: "0.6rem" }}>
          {busy ? "Building…" : `Create form with ${drafts.length} field(s)`}
        </button>
      )}
    </>
  );
}

export function VersionsPanel({
  documentId, refreshKey, notify, onRestored,
}: {
  documentId: string;
  refreshKey: number;
  notify: (message: string, tone?: "ok" | "error") => void;
  onRestored: (message: string) => void;
}) {
  const [history, setHistory] = useState<Awaited<
    ReturnType<typeof api.versions>
  > | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.versions(documentId)
      .then(setHistory)
      .catch((e) => notify((e as Error).message, "error"));
  }, [documentId, refreshKey, notify]);

  if (!history) return <div className="row"><span className="spinner" /> Loading…</div>;

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>{history.note}</div>

      {history.versions.map((version) => (
        <div key={version.version} className="item" style={{ cursor: "default" }}>
          <div className="spread">
            <strong className="small">
              v{version.version} · {version.label}
            </strong>
            {version.version === history.current && (
              <span className="badge ok">current</span>
            )}
          </div>
          <div className="small muted" style={{ marginTop: 2 }}>
            {(version.size_bytes / 1024).toFixed(0)} KB ·{" "}
            {version.created_at.slice(0, 19).replace("T", " ")}
          </div>

          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn sm ghost" onClick={async () => {
              const blob = await api.downloadBlob(documentId, version.version);
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = `v${version.version}.pdf`;
              link.click();
              URL.revokeObjectURL(url);
            }}>Download</button>

            {version.version !== history.current && (
              <button className="btn sm" disabled={busy} onClick={async () => {
                setBusy(true);
                try {
                  const result = await api.restoreVersion(documentId, version.version);
                  onRestored(result.note);
                } catch (e) {
                  notify((e as Error).message, "error");
                } finally {
                  setBusy(false);
                }
              }}>Restore</button>
            )}
          </div>
        </div>
      ))}
    </>
  );
}

/** Turn a dragged rectangle into a draft field. */
export function draftFromRect(type: string, page: number, rect: Rect,
                              index: number): DraftField {
  return {
    key: `${Date.now()}-${index}`,
    // A valid starting name: the API requires it to begin with a letter.
    name: `${type}_${index + 1}`,
    type,
    page,
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    // Give tiny drags a usable minimum rather than rejecting them.
    width: Math.max(Math.round(rect.width), type === "checkbox" ? 14 : 60),
    height: Math.max(Math.round(rect.height), type === "checkbox" ? 14 : 18),
    required: false,
    options: type === "dropdown" ? "Option A, Option B" : "",
  };
}

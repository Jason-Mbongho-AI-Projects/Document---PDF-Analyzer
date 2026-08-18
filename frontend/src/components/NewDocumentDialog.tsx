/**
 * Compose a document here rather than uploading one.
 *
 * Two ways in, because they are genuinely different intentions: someone with
 * words already written wants to paste them and get a PDF; someone starting
 * from nothing wants blank pages to draw, stamp or sign on. Mixing them into
 * one form makes both worse, so they are separate tabs over one action.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Props {
  workspaceId: string;
  onClose: () => void;
  onCreated: (documentId: string, filename: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

type Mode = "write" | "blank";

export function NewDocumentDialog({ workspaceId, onClose, onCreated, notify }: Props) {
  const [mode, setMode] = useState<Mode>("write");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  // Held as text, not a number: clamping on every keystroke means the field
  // cannot be cleared to retype it — you empty it, it snaps back to 1, and
  // the next digit lands after that 1. Clamp when it is used instead.
  const [pages, setPages] = useState("1");
  const [pageSize, setPageSize] = useState<"letter" | "a4">("letter");
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement>(null);

  useEffect(() => { firstField.current?.focus(); }, []);

  // Escape closes, as it does everywhere else in the app.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // The filename follows the title unless the title is empty, which is what
  // people expect from every other editor.
  const filename = (title.trim() || "Untitled") + ".pdf";

  async function create() {
    setBusy(true);
    try {
      const result = await api.createDocument({
        workspace_id: workspaceId,
        filename,
        title: mode === "write" ? title.trim() : "",
        content: mode === "write" ? content : "",
        blank_pages: mode === "blank" ? pageCount : 1,
        page_size: pageSize,
      });
      notify(`Created ${result.document.filename}`, "ok");
      onCreated(result.document.id, result.document.filename);
    } catch (e) {
      notify((e as Error).message, "error");
      setBusy(false);
    }
  }

  const nothingToWrite = mode === "write" && !content.trim() && !title.trim();
  const pageCount = Math.min(200, Math.max(1, Math.floor(Number(pages) || 1)));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal compose" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-label="New document">
        <h3>New document</h3>

        <div className="row seg" role="tablist" style={{ marginBottom: "0.9rem" }}>
          <button
            className={`btn sm ${mode === "write" ? "primary" : "ghost"}`}
            role="tab" aria-selected={mode === "write"}
            onClick={() => setMode("write")}>
            Write it
          </button>
          <button
            className={`btn sm ${mode === "blank" ? "primary" : "ghost"}`}
            role="tab" aria-selected={mode === "blank"}
            onClick={() => setMode("blank")}>
            Blank pages
          </button>
        </div>

        <label className="field">
          <span className="small muted">Title</span>
          <input
            ref={firstField}
            className="input"
            placeholder="Untitled"
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        {mode === "write" ? (
          <label className="field">
            <span className="small muted">
              Content — <code>#</code> for a heading, <code>-</code> for a bullet,
              a blank line between paragraphs
            </span>
            <textarea
              className="input compose-body"
              placeholder={"# Agenda\n\n- Budget\n- Hiring\n\nNotes go here."}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={12}
            />
          </label>
        ) : (
          <label className="field">
            <span className="small muted">How many blank pages</span>
            <input
              className="input"
              type="number"
              min={1}
              max={200}
              value={pages}
              onChange={(e) => setPages(e.target.value)}
              onBlur={() => setPages(String(pageCount))}
            />
          </label>
        )}

        <label className="field">
          <span className="small muted">Page size</span>
          <select className="input" value={pageSize}
                  onChange={(e) => setPageSize(e.target.value as "letter" | "a4")}>
            <option value="letter">Letter — 8.5 × 11 in</option>
            <option value="a4">A4 — 210 × 297 mm</option>
          </select>
        </label>

        <p className="small muted" style={{ marginTop: "0.6rem" }}>
          {mode === "blank"
            ? `${pageCount} blank page${pageCount === 1 ? "" : "s"}, saved as `
            : "Saved as "}
          <strong>{filename}</strong>. You can edit, sign, redact and export it
          like anything else in the library.
        </p>

        <div className="row" style={{ justifyContent: "flex-end", marginTop: "1rem" }}>
          <button className="btn ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" onClick={create}
                  disabled={busy || nothingToWrite}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

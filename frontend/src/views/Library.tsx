/**
 * Document library: create, upload, browse, open, archive and delete.
 *
 * Delete is permanent — it removes the database rows and every stored byte
 * including all versions — so it is always behind an explicit confirmation
 * that names what is going and says it cannot be undone. Archive is offered
 * alongside it because "get it off my dashboard" is usually what someone
 * actually wants, and that one is reversible.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DocumentSummary, type Workspace } from "../api";
import { NewDocumentDialog } from "../components/NewDocumentDialog";
import { Thumbnail } from "../components/Thumbnail";

/* What the picker offers. The server checks the bytes rather than trusting
   any of this, so the list is a convenience for the file dialog, not the
   security boundary. */
const ACCEPTED = [
  "application/pdf",
  ".pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.odt,.rtf,.txt,.md,.csv,.html,.htm",
  ".png,.jpg,.jpeg,.tif,.tiff,.webp,.bmp,.gif",
].join(",");

interface Props {
  workspace: Workspace;
  onOpen: (documentId: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

function humanSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

type Pending = { ids: string[]; names: string[] } | null;

export function Library({ workspace, onOpen, notify }: Props) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [loading, setLoading] = useState(true);
  const [composing, setComposing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.documents(workspace.id, search, showArchived);
      setDocuments(result.items);
      // Drop any selection that no longer exists.
      setSelected((current) => {
        const ids = new Set(result.items.map((d) => d.id));
        return new Set([...current].filter((id) => ids.has(id)));
      });
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }, [workspace.id, search, showArchived, notify]);

  useEffect(() => { load(); }, [load]);

  // Documents are processed by a background worker, so poll while anything
  // is still in flight rather than leaving a stale status on screen.
  useEffect(() => {
    if (!documents.some((d) => d.status === "processing")) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [documents, load]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    for (const file of Array.from(files)) {
      try {
        await api.upload(workspace.id, file);
        notify(`Uploaded ${file.name}`, "ok");
      } catch (e) {
        notify(`${file.name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(false);
    await load();
  }

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setBusy(true);

    let removed = 0;
    for (const id of pendingDelete.ids) {
      try {
        await api.deleteDocument(id);
        removed += 1;
      } catch (e) {
        notify((e as Error).message, "error");
      }
    }

    setPendingDelete(null);
    setSelected(new Set());
    setBusy(false);
    await load();
    if (removed) {
      notify(`Deleted ${removed} document${removed === 1 ? "" : "s"}`, "ok");
    }
  }

  async function setArchived(ids: string[], archived: boolean) {
    setBusy(true);
    for (const id of ids) {
      try {
        archived ? await api.archiveDocument(id) : await api.unarchiveDocument(id);
      } catch (e) {
        notify((e as Error).message, "error");
      }
    }
    setSelected(new Set());
    setBusy(false);
    await load();
    notify(archived ? "Archived" : "Restored", "ok");
  }

  const chosen = documents.filter((d) => selected.has(d.id));

  return (
    <div
      className={`library ${over ? "dragging" : ""}`}
      // The whole page is the drop target, which is what people try first.
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={(e) => {
        // Only clear when the pointer genuinely leaves the library, not when
        // it crosses between children — otherwise the overlay flickers.
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        upload(e.dataTransfer.files);
      }}
    >
      <div className="library-head">
        <div className="library-title">
          <h2>{workspace.name}</h2>
          <p className="small muted">
            {documents.length === 0 ? "No documents yet"
              : `${documents.length} document${documents.length === 1 ? "" : "s"}`}
            {showArchived ? " · including archived" : ""}
          </p>
        </div>

        <div className="library-controls">
          <input
            className="input search"
            placeholder="Search filenames…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search filenames"
          />
          <label className="row small muted toggle" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)} />
            Archived
          </label>
          {/* Upload is the primary action, so it sits where the eye lands
              rather than inside a panel competing with the documents.
              Creating is offered beside it because an empty workspace has
              nothing to upload yet. */}
          <button className="btn" disabled={busy}
            onClick={() => setComposing(true)}>
            New document
          </button>
          <button className="btn primary" disabled={busy}
            onClick={() => fileRef.current?.click()}>
            {busy ? "Working…" : "Upload"}
          </button>
        </div>
      </div>

      {selected.size > 0 && (
        <div className="card selection-bar">
          <strong className="small">{selected.size} selected</strong>
          <div style={{ flex: 1 }} />
          <button className="btn sm" disabled={busy}
            onClick={() => setArchived([...selected], true)}>
            Archive
          </button>
          {showArchived && (
            <button className="btn sm" disabled={busy}
              onClick={() => setArchived([...selected], false)}>
              Restore
            </button>
          )}
          <button className="btn sm danger" disabled={busy}
            onClick={() => setPendingDelete({
              ids: chosen.map((d) => d.id),
              names: chosen.map((d) => d.filename),
            })}>
            Delete
          </button>
          <button className="btn sm ghost" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      <input ref={fileRef} type="file" accept={ACCEPTED} multiple hidden
        onChange={(e) => upload(e.target.files)} />

      {/* Dropping still works anywhere on the library. The target only becomes
          visible while something is being dragged over it — a permanent
          dashed box takes the top of the page from the documents, which are
          what the page is for. */}
      {over && (
        <div className="drop-veil">
          <div className="drop-veil-inner">
            <strong>Drop to upload</strong>
            <span className="small muted">
              PDF, Word, Excel, PowerPoint, text, HTML or an image — anything
              else becomes a PDF on arrival. Content is checked by magic bytes,
              not by filename.
            </span>
          </div>
        </div>
      )}

      {loading ? (
        <div className="row"><span className="spinner" /> Loading…</div>
      ) : documents.length === 0 ? (
        <div className="empty first-run">
          <div className="first-run-mark" aria-hidden>◈</div>
          <h4>{showArchived ? "Nothing archived" : "This workspace is empty"}</h4>
          <p className="small">
            {showArchived
              ? "Documents you archive will appear here."
              : "Drop a file anywhere on this page, upload one, or write a " +
                "new document from scratch. Word, Excel, PowerPoint, text, " +
                "HTML and images all become PDFs on arrival, so you can read, " +
                "edit, redact, sign, convert and ask questions about any of them."}
          </p>
          {!showArchived && (
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn primary" disabled={busy}
                onClick={() => fileRef.current?.click()}>
                {busy ? "Working…" : "Upload a document"}
              </button>
              <button className="btn" disabled={busy}
                onClick={() => setComposing(true)}>
                Create one instead
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="doc-grid">
          {documents.map((doc) => (
            <div key={doc.id}
              className={`doc-card ${selected.has(doc.id) ? "picked" : ""}`}>
              {/* The first page is the fastest way to recognise a document.
                  A list of filenames makes you read; a wall of covers lets
                  you look. Only rendered once the card is on screen. */}
              <button className="doc-cover" onClick={() => onOpen(doc.id)}
                      aria-label={`Open ${doc.filename}`} tabIndex={-1}>
                {doc.status === "ready" ? (
                  <Thumbnail
                    documentId={doc.id}
                    page={1}
                    active={false}
                    selected={false}
                    version={1}
                    onClick={() => onOpen(doc.id)}
                  />
                ) : (
                  <div className="doc-cover-empty small muted">
                    {doc.status === "failed" ? "Could not be read" : "Preparing…"}
                  </div>
                )}
              </button>

              <div className="doc-card-top">
                <input
                  type="checkbox"
                  checked={selected.has(doc.id)}
                  onChange={() => toggle(doc.id)}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={`Select ${doc.filename}`}
                />
                <button className="doc-open" onClick={() => onOpen(doc.id)}
                  title={doc.filename}>
                  {doc.filename}
                </button>
              </div>

              <div className="spread" style={{ marginTop: 6 }}>
                <span className="small muted">
                  {doc.page_count ? `${doc.page_count} pages · ` : ""}
                  {humanSize(doc.size_bytes)}
                </span>
                <span className={`badge ${
                  doc.is_archived ? "warn"
                    : doc.status === "ready" ? "ok"
                      : doc.status === "failed" ? "bad" : "info"}`}>
                  {doc.is_archived ? "archived" : doc.status}
                </span>
              </div>

              <div className="row doc-actions">
                <button className="btn sm ghost" onClick={() => onOpen(doc.id)}>
                  Open
                </button>
                <button className="btn sm ghost"
                  onClick={() => setArchived([doc.id], !doc.is_archived)}
                  title={doc.is_archived
                    ? "Bring it back to the main list"
                    : "Hide it from the list — reversible"}>
                  {doc.is_archived ? "Restore" : "Archive"}
                </button>
                <button className="btn sm ghost" style={{ color: "var(--bad)" }}
                  onClick={() => setPendingDelete({
                    ids: [doc.id], names: [doc.filename],
                  })}
                  title="Permanently delete this document and all its versions">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {composing && (
        <NewDocumentDialog
          workspaceId={workspace.id}
          notify={notify}
          onClose={() => setComposing(false)}
          onCreated={(id) => { setComposing(false); load(); onOpen(id); }}
        />
      )}

      {pendingDelete && (
        <div className="modal-backdrop" onClick={() => setPendingDelete(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              Delete {pendingDelete.ids.length} document
              {pendingDelete.ids.length === 1 ? "" : "s"}?
            </h3>

            <div className="error" style={{ marginBottom: "0.9rem" }}>
              This cannot be undone. Every version, annotation, signature
              request and stored file is removed permanently.
            </div>

            <div style={{ maxHeight: 200, overflow: "auto", marginBottom: "1rem" }}>
              {pendingDelete.names.map((name, index) => (
                <div key={index} className="small" style={{ padding: "2px 0" }}>
                  {name}
                </div>
              ))}
            </div>

            <p className="small muted" style={{ marginTop: 0 }}>
              If you only want it off your dashboard, archive it instead —
              that is reversible.
            </p>

            <div className="row">
              <button className="btn danger" disabled={busy} onClick={confirmDelete}>
                {busy ? "Deleting…" : "Yes, delete permanently"}
              </button>
              <button className="btn" disabled={busy} onClick={() => {
                setArchived(pendingDelete.ids, true);
                setPendingDelete(null);
              }}>
                Archive instead
              </button>
              <button className="btn ghost" onClick={() => setPendingDelete(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

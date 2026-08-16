/** Document library: upload, browse, open. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DocumentSummary, type Workspace } from "../api";

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

export function Library({ workspace, onOpen, notify }: Props) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [loading, setLoading] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.documents(workspace.id, search);
      setDocuments(result.items);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }, [workspace.id, search, notify]);

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

  return (
    <div className="library">
      <div className="library-head">
        <h2>{workspace.name}</h2>
        <span className="badge info">{documents.length} document(s)</span>
        <div style={{ flex: 1 }} />
        <input
          className="input"
          style={{ maxWidth: 240 }}
          placeholder="Search filenames…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div
        className={`dropzone ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}
      >
        <input ref={fileRef} type="file" accept="application/pdf" multiple hidden
          onChange={(e) => upload(e.target.files)} />
        <p className="muted" style={{ marginTop: 0 }}>
          Drop PDFs here, or
        </p>
        <button className="btn primary" disabled={busy} onClick={() => fileRef.current?.click()}>
          {busy ? "Uploading…" : "Choose files"}
        </button>
        <p className="small muted" style={{ marginBottom: 0 }}>
          PDF only. Content is validated by magic bytes, not by filename.
        </p>
      </div>

      {loading ? (
        <div className="row"><span className="spinner" /> Loading…</div>
      ) : documents.length === 0 ? (
        <div className="empty">
          <h4>No documents yet</h4>
          <p className="small">Upload a PDF to get started.</p>
        </div>
      ) : (
        <div className="doc-grid">
          {documents.map((doc) => (
            <button key={doc.id} className="doc-card" onClick={() => onOpen(doc.id)}>
              <h4 title={doc.filename}>{doc.filename}</h4>
              <div className="spread">
                <span className="small muted">
                  {doc.page_count ? `${doc.page_count} pages · ` : ""}
                  {humanSize(doc.size_bytes)}
                </span>
                <span className={`badge ${
                  doc.status === "ready" ? "ok"
                    : doc.status === "failed" ? "bad" : "info"}`}>
                  {doc.status}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

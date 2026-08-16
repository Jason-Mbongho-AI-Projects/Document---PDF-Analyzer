/**
 * Document-level structure: properties, hidden data, bookmarks, and writing
 * comments into the file.
 *
 * These are the things Acrobat exposes that a viewer does not, and each one
 * had a working API and no way to reach it.
 *
 * The hidden-data section is the one that matters most. A PDF carries author
 * names, producing software, XMP history, embedded files and JavaScript that
 * nothing on screen reveals, and sending a document without clearing them has
 * leaked more than most redaction failures. What is present is listed, and
 * what removal did is reported rather than assumed.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  documentId: string;
  pageCount: number;
  annotationCount: number;
  onSaved: (message: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

type Bookmark = { title: string; page: number; depth: number };

export function DocumentPanel({
  documentId, pageCount, annotationCount, onSaved, notify,
}: Props) {
  const [props, setProps] = useState<Awaited<
    ReturnType<typeof api.properties>> | null>(null);
  const [meta, setMeta] = useState<Record<string, string>>({});
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, o] = await Promise.all([
        api.properties(documentId),
        api.outline(documentId),
      ]);
      setProps(p);
      setMeta(p.metadata);
      setBookmarks(o.entries);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }, [documentId, notify]);

  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<{ note?: string | null }>,
                     fallback: string) {
    setBusy(true);
    try {
      const result = await action();
      onSaved(result.note || fallback);
      await load();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const hidden = props?.hidden_data ?? {};
  const carries = Object.entries(hidden).filter(([, v]) => v);

  return (
    <>
      <div className="meta">Properties</div>
      {(["title", "author", "subject", "keywords"] as const).map((field) => (
        <label key={field} className="field">
          <span>{field}</span>
          <input className="input" value={meta[field] ?? ""}
            onChange={(e) => setMeta({ ...meta, [field]: e.target.value })} />
        </label>
      ))}
      <button className="btn sm" disabled={busy}
        onClick={() => run(() => api.setProperties(documentId, {
          title: meta.title ?? "", author: meta.author ?? "",
          subject: meta.subject ?? "", keywords: meta.keywords ?? "",
        }), "Properties saved")}>
        Save properties
      </button>

      <div className="meta" style={{ marginTop: 16 }}>Hidden data</div>
      {props ? (
        carries.length ? (
          <>
            <p className="small muted" style={{ marginTop: 0 }}>
              This file carries information beyond what you can see:
            </p>
            {carries.map(([key, value]) => (
              <div key={key} className="small">
                • {key.replace(/_/g, " ")}
                {typeof value === "number" ? `: ${value}` : ""}
              </div>
            ))}
          </>
        ) : (
          <p className="small muted" style={{ marginTop: 0 }}>
            None of the hidden data checked for is present.
          </p>
        )
      ) : (
        <p className="small muted">Reading…</p>
      )}

      <button className="btn sm" style={{ marginTop: 8 }} disabled={busy}
        title="Remove metadata, JavaScript and embedded files"
        onClick={() => run(() => api.sanitise(documentId), "Sanitised")}>
        Remove hidden data
      </button>
      <p className="small muted">
        Removes metadata, embedded files and JavaScript. Earlier versions keep
        it — delete them if the original must not survive.
      </p>

      <div className="meta" style={{ marginTop: 16 }}>Bookmarks</div>
      {bookmarks.map((entry, index) => (
        <div key={index} className="row small" style={{ padding: "2px 0" }}>
          <input className="input" style={{ marginLeft: entry.depth * 14 }}
            value={entry.title} aria-label={`Bookmark ${index + 1} title`}
            onChange={(e) => setBookmarks(bookmarks.map((b, i) =>
              i === index ? { ...b, title: e.target.value } : b))} />
          <input className="input" type="number" min={1} max={pageCount}
            style={{ maxWidth: 70 }} value={entry.page}
            aria-label={`Bookmark ${index + 1} page`}
            onChange={(e) => setBookmarks(bookmarks.map((b, i) =>
              i === index ? { ...b, page: Number(e.target.value) || 1 } : b))} />
          <button className="btn sm ghost" title="Indent"
            onClick={() => setBookmarks(bookmarks.map((b, i) =>
              i === index ? { ...b, depth: Math.min(8, b.depth + 1) } : b))}>→</button>
          <button className="btn sm ghost" title="Outdent"
            onClick={() => setBookmarks(bookmarks.map((b, i) =>
              i === index ? { ...b, depth: Math.max(0, b.depth - 1) } : b))}>←</button>
          <button className="btn sm ghost" style={{ color: "var(--bad)" }}
            aria-label={`Remove bookmark ${index + 1}`}
            onClick={() => setBookmarks(bookmarks.filter((_, i) => i !== index))}>
            ×
          </button>
        </div>
      ))}
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn sm" onClick={() => setBookmarks(
          [...bookmarks, { title: "New bookmark", page: 1, depth: 0 }])}>
          Add bookmark
        </button>
        <button className="btn sm primary" disabled={busy}
          onClick={() => run(() => api.setOutline(documentId, bookmarks),
                             "Bookmarks saved")}>
          Save bookmarks
        </button>
      </div>

      <div className="meta" style={{ marginTop: 16 }}>Comments in the file</div>
      <p className="small muted" style={{ marginTop: 0 }}>
        Annotations are kept alongside the document so marking it up never
        rewrites it — which means a download does not contain them. This writes
        the current {annotationCount} annotation(s) into the page as a new
        version, for sending to someone outside this app.
      </p>
      <button className="btn sm" disabled={busy || annotationCount === 0}
        title={annotationCount === 0 ? "There are no annotations yet" : undefined}
        onClick={() => run(() => api.flattenAnnotations(documentId),
                           "Comments written into the document")}>
        Write {annotationCount} comment(s) into the PDF
      </button>
    </>
  );
}

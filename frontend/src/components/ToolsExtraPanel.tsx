/**
 * Links, attachments, Bates numbering and scan cleanup.
 *
 * Two of these are as much about safety as convenience. A link can carry a
 * javascript: action and an attachment can carry an executable — the security
 * scanner reports both, and the tools that add them refuse to be the route
 * such a thing takes in. Being able to see and strip what a document already
 * carries is the other half of detecting it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Props {
  documentId: string;
  pageCount: number;
  onSaved: (message: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

export function ExtrasPanel({ documentId, pageCount, onSaved, notify }: Props) {
  const [links, setLinks] = useState<Awaited<ReturnType<typeof api.links>> | null>(null);
  const [files, setFiles] = useState<Awaited<ReturnType<typeof api.attachments>> | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // link form
  const [url, setUrl] = useState("");
  const [linkPage, setLinkPage] = useState(1);

  // bates
  const [prefix, setPrefix] = useState("");
  const [digits, setDigits] = useState(6);
  const [startAt, setStartAt] = useState(1);

  // enhancement
  const [deskew, setDeskew] = useState(true);
  const [despeckle, setDespeckle] = useState(true);
  const [contrast, setContrast] = useState(true);
  const [binarise, setBinarise] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, f] = await Promise.all([
        api.links(documentId), api.attachments(documentId),
      ]);
      setLinks(l);
      setFiles(f);
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

  return (
    <>
      <div className="meta">Links</div>
      {links?.links.length ? links.links.map((link, index) => (
        <div key={index} className="row small" style={{ padding: "2px 0" }}>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
            p{link.page} · {link.target ?? link.kind}
          </span>
          <button className="btn sm ghost" style={{ color: "var(--bad)" }}
            disabled={busy} aria-label={`Remove link ${index + 1}`}
            onClick={() => run(
              () => api.removeLinks(documentId, link.page, link.index),
              "Link removed")}>×</button>
        </div>
      )) : <p className="small muted" style={{ marginTop: 0 }}>No links.</p>}

      <div className="row" style={{ marginTop: 6 }}>
        <input className="input" value={url} placeholder="https://…"
          onChange={(e) => setUrl(e.target.value)} />
        <input className="input" type="number" min={1} max={pageCount}
          style={{ maxWidth: 70 }} value={linkPage} aria-label="Link page"
          onChange={(e) => setLinkPage(Number(e.target.value) || 1)} />
      </div>
      <p className="small muted" style={{ marginTop: 4 }}>
        Added across the top of the page. Only http, https and mailto are
        accepted — a PDF link is a place behaviour can hide.
      </p>
      <button className="btn sm" disabled={busy || !url.trim()}
        onClick={() => run(() => api.addLink(
          documentId, linkPage,
          { x: 60, y: 60, width: 300, height: 18 }, url.trim()),
          "Link added")}>
        Add link
      </button>

      <div className="meta" style={{ marginTop: 16 }}>Attachments</div>
      {files?.attachments.length ? files.attachments.map((file) => (
        <div key={file.name} className="row small" style={{ padding: "2px 0" }}>
          <a href={api.attachmentUrl(documentId, file.name)}
             style={{ flex: 1 }}>{file.name}</a>
          <span className="muted">{(file.size_bytes / 1024).toFixed(1)} KB</span>
          {file.risky && <span className="badge bad">risky</span>}
          <button className="btn sm ghost" style={{ color: "var(--bad)" }}
            disabled={busy} aria-label={`Remove ${file.name}`}
            onClick={() => run(() => api.removeAttachment(documentId, file.name),
                               "Attachment removed")}>×</button>
        </div>
      )) : <p className="small muted" style={{ marginTop: 0 }}>Nothing attached.</p>}

      <input ref={fileRef} type="file" hidden
        onChange={(e) => {
          const chosen = e.target.files?.[0];
          if (chosen) run(() => api.attachFile(documentId, chosen), "File attached");
          e.target.value = "";
        }} />
      <button className="btn sm" disabled={busy}
        onClick={() => fileRef.current?.click()}>Attach a file</button>
      <p className="small muted">
        Executables are refused: a document carrying one is treated as
        malicious by most scanners, and rightly so.
      </p>

      <div className="meta" style={{ marginTop: 16 }}>Bates numbering</div>
      <div className="row">
        <input className="input" value={prefix} placeholder="Prefix e.g. ACME-"
          onChange={(e) => setPrefix(e.target.value)} />
        <input className="input" type="number" min={1} max={12}
          style={{ maxWidth: 66 }} value={digits} aria-label="Digits"
          onChange={(e) => setDigits(Number(e.target.value) || 6)} />
        <input className="input" type="number" min={0}
          style={{ maxWidth: 80 }} value={startAt} aria-label="Start at"
          onChange={(e) => setStartAt(Number(e.target.value) || 0)} />
      </div>
      <p className="small muted" style={{ marginTop: 4 }}>
        First page will read{" "}
        <strong>{prefix}{String(startAt).padStart(digits, "0")}</strong>.
      </p>
      <button className="btn sm" disabled={busy}
        onClick={() => run(() => api.bates(documentId, {
          prefix, digits, start_at: startAt }), "Bates numbering applied")}>
        Apply Bates numbers
      </button>

      <div className="meta" style={{ marginTop: 16 }}>Clean up a scan</div>
      {([["Straighten", deskew, setDeskew],
         ["Remove speckles", despeckle, setDespeckle],
         ["Normalise contrast", contrast, setContrast],
         ["Black and white", binarise, setBinarise]] as
        [string, boolean, (v: boolean) => void][]).map(([label, value, set]) => (
        <label key={label} className="row small" style={{ cursor: "pointer" }}>
          <input type="checkbox" checked={value}
            onChange={(e) => set(e.target.checked)} />
          {label}
        </label>
      ))}
      <p className="small muted">
        Rebuilds each page as an image, so it suits scans and not documents
        that already have real text. Run OCR afterwards to give it a text
        layer again.
      </p>
      <button className="btn sm" disabled={busy}
        onClick={() => run(() => api.enhanceScan(documentId, {
          deskew, despeckle, contrast, binarise }), "Scan cleaned")}>
        Clean up
      </button>
    </>
  );
}

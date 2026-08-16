/**
 * Organise, Edit and Combine.
 *
 * These operations existed on the API from the beginning but had no interface:
 * crop, split, headers and footers, compression, password protection and
 * unlocking were all reachable only by calling the endpoints directly, and
 * combining documents had no endpoint at all. A capability nobody can find is
 * not a feature.
 *
 * Every operation appends a version. Nothing here overwrites a document, so
 * the worst outcome of a mistake is an extra version to restore from.
 */
import { useEffect, useState } from "react";
import { api, type DocumentSummary } from "../api";

interface Common {
  documentId: string;
  pageCount: number;
  /** Pages ticked in the thumbnail rail; empty means "all pages". */
  selectedPages: number[];
  onSaved: (message: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

/** Parse "1-3, 7, 9-10" into page numbers, bounded by the document. */
export function parseRanges(spec: string, pageCount: number): number[] {
  const wanted = new Set<number>();
  for (const part of spec.split(",")) {
    const bounds = part.trim().split("-").map((n) => parseInt(n, 10));
    if (bounds.length === 2 && !bounds.some(isNaN)) {
      for (let p = bounds[0]; p <= bounds[1]; p++) wanted.add(p);
    } else if (!isNaN(bounds[0])) {
      wanted.add(bounds[0]);
    }
  }
  return [...wanted].filter((p) => p >= 1 && p <= pageCount).sort((a, b) => a - b);
}

/** Turn a page list into contiguous [start, end] ranges for splitting. */
export function toRanges(pages: number[]): [number, number][] {
  const ranges: [number, number][] = [];
  for (const page of pages) {
    const last = ranges[ranges.length - 1];
    if (last && page === last[1] + 1) last[1] = page;
    else ranges.push([page, page]);
  }
  return ranges;
}

function useRunner(onSaved: (m: string) => void,
                   notify: (m: string, t?: "ok" | "error") => void) {
  const [busy, setBusy] = useState(false);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      onSaved(label);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return { busy, run };
}

// ------------------------------------------------------------- organise

export function OrganisePanel({
  documentId, pageCount, selectedPages, onSaved, notify,
}: Common) {
  const { busy, run } = useRunner(onSaved, notify);
  const [spec, setSpec] = useState("");
  const [splitParts, setSplitParts] = useState<
    { version: number; pages: string; size_bytes: number }[] | null>(null);

  // The rail selection is the default, but a typed range wins when given, so
  // the panel is usable without hunting through thumbnails on a long document.
  const typed = parseRanges(spec, pageCount);
  const pages = typed.length ? typed : selectedPages;
  const target = pages.length ? pages : Array.from({ length: pageCount }, (_, i) => i + 1);
  const describe = pages.length ? `${pages.length} page(s)` : `all ${pageCount} pages`;

  return (
    <>
      <label className="field">
        <span>Pages</span>
        <input className="input" value={spec} placeholder="e.g. 1-3, 7 — blank uses your selection"
          onChange={(e) => setSpec(e.target.value)} />
      </label>
      <p className="small muted" style={{ marginTop: 0 }}>Acting on {describe}.</p>

      <div className="meta">Arrange</div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <button className="btn sm" disabled={busy}
          onClick={() => run("Rotated", () => api.rotate(documentId, target, 90))}>
          Rotate right
        </button>
        <button className="btn sm" disabled={busy}
          onClick={() => run("Rotated", () => api.rotate(documentId, target, -90))}>
          Rotate left
        </button>
        <button className="btn sm" disabled={busy}
          onClick={() => run("Pages duplicated",
            () => api.duplicatePages(documentId, target))}>
          Duplicate
        </button>
        <button className="btn sm danger" disabled={busy || target.length >= pageCount}
          title={target.length >= pageCount
            ? "A document must keep at least one page"
            : "Remove these pages (a new version — the original is kept)"}
          onClick={() => run("Pages deleted",
            () => api.deletePages(documentId, target))}>
          Delete pages
        </button>
      </div>

      <div className="meta" style={{ marginTop: 14 }}>Extract</div>
      <p className="small muted" style={{ marginTop: 0 }}>
        Downloads the chosen pages as a separate PDF. This document is unchanged.
      </p>
      <button className="btn sm" disabled={busy || !target.length}
        onClick={() => run("Extracted", async () => {
          const blob = await api.extractPages(documentId, target);
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = "extract.pdf";
          link.click();
          URL.revokeObjectURL(url);
        })}>
        Download {target.length} page(s)
      </button>

      <div className="meta" style={{ marginTop: 14 }}>Split</div>
      <p className="small muted" style={{ marginTop: 0 }}>
        Each run of consecutive pages becomes its own version you can download
        from the Versions tab.
      </p>
      <button className="btn sm" disabled={busy || !target.length}
        onClick={() => run("Split", async () => {
          const result = await api.splitDocument(documentId, toRanges(target));
          setSplitParts(result.parts);
        })}>
        Split into {toRanges(target).length} part(s)
      </button>

      {splitParts && (
        <div style={{ marginTop: 10 }}>
          {splitParts.map((part) => (
            <div key={part.version} className="small muted">
              pages {part.pages} → version {part.version}
            </div>
          ))}
        </div>
      )}

      <div className="meta" style={{ marginTop: 14 }}>Crop</div>
      <CropControls documentId={documentId} pages={target}
                    busy={busy} run={run} />
    </>
  );
}

function CropControls({
  documentId, pages, busy, run,
}: {
  documentId: string;
  pages: number[];
  busy: boolean;
  run: (label: string, action: () => Promise<unknown>) => Promise<void>;
}) {
  const [margins, setMargins] = useState({ left: 0, bottom: 0, right: 0, top: 0 });

  return (
    <>
      <p className="small muted" style={{ marginTop: 0 }}>
        Points to trim from each edge. 72 points is one inch.
      </p>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        {(["left", "top", "right", "bottom"] as const).map((edge) => (
          <label key={edge} className="small" style={{ flex: "1 1 46%" }}>
            {edge}
            <input className="input" type="number" min={0} value={margins[edge]}
              onChange={(e) => setMargins({
                ...margins, [edge]: Math.max(0, Number(e.target.value) || 0),
              })} />
          </label>
        ))}
      </div>
      <button className="btn sm" style={{ marginTop: 8 }}
        disabled={busy || !Object.values(margins).some((v) => v > 0)}
        onClick={() => run("Cropped",
          () => api.cropPages(documentId, pages, margins))}>
        Crop {pages.length} page(s)
      </button>
    </>
  );
}

// ----------------------------------------------------------------- edit

// Composition applies to the whole document by design: a watermark or page
// numbering on a subset of pages is almost never what someone means, and the
// API defaults to every page when none are named.
export function EditPanel({
  documentId, onSaved, notify,
}: Omit<Common, "pageCount" | "selectedPages">) {
  const { busy, run } = useRunner(onSaved, notify);
  const [watermarkText, setWatermarkText] = useState("");
  const [numberPosition, setNumberPosition] = useState("bottom-center");
  const [header, setHeader] = useState("");
  const [footer, setFooter] = useState("");
  const [preset, setPreset] = useState("balanced");
  const [saving, setSaving] = useState<string | null>(null);

  return (
    <>
      <div className="meta">Watermark</div>
      <div className="row">
        <input className="input" value={watermarkText} placeholder="DRAFT"
          onChange={(e) => setWatermarkText(e.target.value)} />
        <button className="btn sm" disabled={busy || !watermarkText.trim()}
          onClick={() => run("Watermarked",
            () => api.watermark(documentId, watermarkText.trim()))}>
          Apply
        </button>
      </div>

      <div className="meta" style={{ marginTop: 14 }}>Page numbers</div>
      <div className="row">
        <select className="input" value={numberPosition}
          onChange={(e) => setNumberPosition(e.target.value)}>
          {["bottom-center", "bottom-left", "bottom-right",
            "top-center", "top-left", "top-right"].map((p) => (
            <option key={p} value={p}>{p.replace("-", " ")}</option>
          ))}
        </select>
        <button className="btn sm" disabled={busy}
          onClick={() => run("Page numbers added",
            () => api.pageNumbers(documentId, numberPosition))}>
          Add
        </button>
      </div>

      <div className="meta" style={{ marginTop: 14 }}>Header and footer</div>
      <input className="input" value={header} placeholder="Header text"
        onChange={(e) => setHeader(e.target.value)} />
      <input className="input" style={{ marginTop: 6 }} value={footer}
        placeholder="Footer text" onChange={(e) => setFooter(e.target.value)} />
      <button className="btn sm" style={{ marginTop: 8 }}
        disabled={busy || !(header.trim() || footer.trim())}
        onClick={() => run("Header and footer added",
          () => api.headerFooter(documentId, {
            header: header.trim(), footer: footer.trim(),
          }))}>
        Apply
      </button>

      <div className="meta" style={{ marginTop: 14 }}>Compress</div>
      <div className="row">
        <select className="input" value={preset}
          onChange={(e) => setPreset(e.target.value)}>
          <option value="maximum-quality">Maximum quality</option>
          <option value="balanced">Balanced</option>
          <option value="maximum-compression">Maximum compression</option>
        </select>
        <button className="btn sm" disabled={busy}
          onClick={() => run("Compressed", async () => {
            const result = await api.compress(documentId, preset);
            setSaving(
              `${(result.original_bytes / 1024).toFixed(0)} KB → ` +
              `${(result.compressed_bytes / 1024).toFixed(0)} KB ` +
              `(${result.reduction_percent}% smaller)`,
            );
          })}>
          Compress
        </button>
      </div>
      {saving && <p className="small muted">{saving}</p>}

      <SecurityControls documentId={documentId} busy={busy} run={run} />

      <p className="small muted" style={{ marginTop: 14 }}>
        Every action here saves a new version. Nothing replaces your original —
        earlier versions stay downloadable from the Versions tab.
      </p>
    </>
  );
}

function SecurityControls({
  documentId, busy, run,
}: {
  documentId: string;
  busy: boolean;
  run: (label: string, action: () => Promise<unknown>) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [unlockPassword, setUnlockPassword] = useState("");

  return (
    <>
      <div className="meta" style={{ marginTop: 14 }}>Password protect</div>
      <div className="row">
        <input className="input" type="password" value={password}
          placeholder="At least 4 characters"
          onChange={(e) => setPassword(e.target.value)} />
        <button className="btn sm" disabled={busy || password.length < 4}
          onClick={() => run("Protected", async () => {
            await api.protect(documentId, password);
            setPassword("");
          })}>
          Protect
        </button>
      </div>
      <p className="small muted" style={{ marginTop: 4 }}>
        AES-256. Earlier versions remain unprotected — delete them if the
        unprotected copy must not survive.
      </p>

      <div className="meta" style={{ marginTop: 14 }}>Unlock</div>
      <div className="row">
        <input className="input" type="password" value={unlockPassword}
          placeholder="Current password"
          onChange={(e) => setUnlockPassword(e.target.value)} />
        <button className="btn sm" disabled={busy || !unlockPassword}
          onClick={() => run("Unlocked", async () => {
            await api.unlock(documentId, unlockPassword);
            setUnlockPassword("");
          })}>
          Unlock
        </button>
      </div>
    </>
  );
}

// -------------------------------------------------------------- combine

export function CombinePanel({
  documentId, workspaceId, onCombined, notify,
}: {
  documentId: string;
  workspaceId: string;
  onCombined: (newDocumentId: string, message: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [available, setAvailable] = useState<DocumentSummary[]>([]);
  const [chosen, setChosen] = useState<string[]>([documentId]);
  const [filename, setFilename] = useState("combined.pdf");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.documents(workspaceId, "", false)
      .then((r) => setAvailable(r.items))
      .catch((e) => notify((e as Error).message, "error"));
  }, [workspaceId, notify]);

  function toggle(id: string) {
    setChosen((current) => current.includes(id)
      ? current.filter((x) => x !== id)
      : [...current, id]);
  }

  function move(index: number, delta: number) {
    const next = [...chosen];
    const to = index + delta;
    if (to < 0 || to >= next.length) return;
    [next[index], next[to]] = [next[to], next[index]];
    setChosen(next);
  }

  const nameOf = (id: string) =>
    available.find((d) => d.id === id)?.filename ?? id.slice(0, 8);

  return (
    <>
      <div className="meta">Documents to combine</div>
      <p className="small muted" style={{ marginTop: 0 }}>
        Tick each document, then set the order. Pages appear in exactly this
        sequence. The originals are not modified.
      </p>

      <div style={{ maxHeight: 200, overflow: "auto", marginBottom: 10 }}>
        {available.map((doc) => (
          <label key={doc.id} className="row small"
                 style={{ padding: "3px 0", cursor: "pointer" }}>
            <input type="checkbox" checked={chosen.includes(doc.id)}
              onChange={() => toggle(doc.id)} />
            <span>{doc.filename}</span>
          </label>
        ))}
        {available.length === 0 && (
          <p className="small muted">No other documents in this workspace.</p>
        )}
      </div>

      {chosen.length > 0 && (
        <>
          <div className="meta">Order</div>
          {chosen.map((id, index) => (
            <div key={id} className="row small" style={{ padding: "2px 0" }}>
              <span style={{ width: 18 }}>{index + 1}.</span>
              <span style={{ flex: 1 }}>{nameOf(id)}</span>
              <button className="btn sm ghost" disabled={index === 0}
                onClick={() => move(index, -1)} aria-label={`Move ${nameOf(id)} up`}>↑</button>
              <button className="btn sm ghost" disabled={index === chosen.length - 1}
                onClick={() => move(index, 1)} aria-label={`Move ${nameOf(id)} down`}>↓</button>
            </div>
          ))}
        </>
      )}

      <label className="field" style={{ marginTop: 10 }}>
        <span>Name for the combined file</span>
        <input className="input" value={filename}
          onChange={(e) => setFilename(e.target.value)} />
      </label>

      <button className="btn primary" disabled={busy || chosen.length < 2}
        title={chosen.length < 2 ? "Choose at least two documents" : undefined}
        onClick={async () => {
          setBusy(true);
          try {
            const result = await api.mergeDocuments(chosen, filename.trim());
            onCombined(result.document.id,
                       `Combined ${chosen.length} documents into ${result.document.filename}`);
          } catch (e) {
            notify((e as Error).message, "error");
          } finally {
            setBusy(false);
          }
        }}>
        {busy ? "Combining…" : `Combine ${chosen.length} document(s)`}
      </button>
    </>
  );
}

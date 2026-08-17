/**
 * The document workspace: thumbnails, canvas, tool strip and side panels.
 */
import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";

import {
  api, type AiSelectionResult, type Annotation, type DocumentDetail,
  type RedactCandidate, type SearchMatch,
} from "../api";
import {
  PdfPage, annotationOverlays, annotationStrokes,
  type DrawTool, type Overlay, type Stroke,
} from "../components/PdfPage";
import { Thumbnail } from "../components/Thumbnail";
import { SelectionToolbar, readSelection, type Selection } from "../components/SelectionToolbar";
import {
  CommentsPanel, FormPanel, RedactPanel, SearchPanel, SecurityPanel,
} from "../components/Panels";
import { AiPanel } from "../components/AiPanel";
import { ComparePanel, ConvertPanel } from "../components/ConvertPanel";
import { SignPanel } from "../components/SignPanel";
import { OcrPanel, TranslatePanel } from "../components/ToolsPanel";
import { AnalysePanel, SummarisePanel } from "../components/SummarisePanel";
import {
  CombinePanel, EditPanel, OrganisePanel, PagesFromPanel,
} from "../components/OrganisePanel";
import { DocumentPanel } from "../components/DocumentPanel";
import { ExtrasPanel } from "../components/ToolsExtraPanel";
import { TextEditPanel } from "../components/TextEditPanel";
import { CommandPalette } from "../components/CommandPalette";
import { clearDraft, readDraft, useAutosaveDraft } from "../useDraft";
import { DraftIndicator } from "../components/Panels";
import { FormBuilderPanel, VersionsPanel, draftFromRect,
  type DraftField } from "../components/FormBuilderPanel";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Tool = "select" | "snapshot" | DrawTool;

/** Colours offered for marks, kept few so the choice is quick. */
const MARK_COLOURS = ["#BE123C", "#1D4ED8", "#047857", "#B45309", "#7C3AED"];
const DRAW_TOOLS: DrawTool[] = ["shape", "arrow", "draw", "textbox", "note"];
/** Size of a note pin, in PDF points. */
const PIN = 18;
type Tab = "summarise" | "analyse" | "ai" | "comments" | "search"
  | "security" | "form" | "builder" | "redact" | "convert" | "compare"
  | "sign" | "translate" | "ocr" | "versions"
  | "organise" | "edit" | "combine" | "text" | "pagesfrom" | "document"
  | "extras";


/**
 * Tools, grouped by the job they do.
 *
 * Flat, the twenty-two tools were a wall to read every time. Grouped, the
 * question becomes "what am I trying to do" — which is how people arrive at a
 * PDF tool — and only one group's worth is ever on screen.
 */
interface ToolSpec { id: Tab; label: string; hint: string }
interface ToolGroup { id: string; label: string; glyph: string; tools: ToolSpec[] }

const TOOL_GROUPS: ToolGroup[] = [
  {
    id: "review", label: "Review", glyph: "◎",
    tools: [
      { id: "comments", label: "Comments", hint: "Notes and mark-up on this document" },
      { id: "search", label: "Search", hint: "Find text and jump to it" },
      { id: "compare", label: "Compare", hint: "Differences against another document" },
      { id: "versions", label: "Versions", hint: "Every saved version, and restore" },
    ],
  },
  {
    id: "ai", label: "Understand", glyph: "✦",
    tools: [
      { id: "summarise", label: "Summarise", hint: "A summary of the whole document" },
      { id: "analyse", label: "Analyse", hint: "Structure, themes and observations" },
      { id: "ai", label: "Ask AI", hint: "Questions answered with page citations" },
      { id: "translate", label: "Translate", hint: "Translate pages or the whole file" },
    ],
  },
  {
    id: "edit", label: "Edit", glyph: "✎",
    tools: [
      { id: "text", label: "Text", hint: "Change, delete or add words on the page" },
      { id: "organise", label: "Pages", hint: "Rotate, delete, split, crop, extract" },
      { id: "edit", label: "Stamp", hint: "Watermark, page numbers, headers, compress" },
      { id: "pagesfrom", label: "Insert", hint: "Take pages from another document" },
      { id: "combine", label: "Combine", hint: "Merge several documents into one" },
    ],
  },
  {
    id: "forms", label: "Forms", glyph: "▤",
    tools: [
      { id: "form", label: "Fill", hint: "Fill in an existing form" },
      { id: "builder", label: "Build", hint: "Add fillable fields to a document" },
      { id: "sign", label: "Sign", hint: "Sign it, or request signatures" },
    ],
  },
  {
    id: "secure", label: "Secure", glyph: "⚿",
    tools: [
      { id: "security", label: "Scan", hint: "Static check for risky constructs" },
      { id: "redact", label: "Redact", hint: "Remove sensitive text for good" },
      { id: "document", label: "Properties", hint: "Metadata, hidden data, bookmarks" },
      { id: "extras", label: "Links & files", hint: "Links, attachments, Bates numbers" },
    ],
  },
  {
    id: "convert", label: "Convert", glyph: "⇄",
    tools: [
      { id: "convert", label: "Export", hint: "Word, Excel, PowerPoint, images, text" },
      { id: "ocr", label: "OCR", hint: "Read text off a scanned page" },
    ],
  },
];

/** Every tool, flattened — used by the command palette. */
const ALL_TOOLS: (ToolSpec & { group: string })[] = TOOL_GROUPS.flatMap(
  (group) => group.tools.map((tool) => ({ ...tool, group: group.label })));

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

interface Props {
  documentId: string;
  onBack: () => void;
  notify: (message: string, tone?: "ok" | "error") => void;
  /** Switch to another document — used after combining creates a new one. */
  onOpenDocument?: (documentId: string) => void;
}

export function Workspace({ documentId, onBack, notify, onOpenDocument }: Props) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pages, setPages] = useState<PDFPageProxy[]>([]);
  const [scale, setScale] = useState(1.25);
  // Set once, when the first page is measured against the viewport. Without it
  // a Letter page renders ~765px wide inside a 390px phone, which does not
  // merely overflow: the browser widens the layout viewport to accommodate it,
  // and every fixed-position element is then positioned against the wrong box.
  const fittedRef = useRef(false);
  const [current, setCurrent] = useState(1);
  const [tool, setTool] = useState<Tool>("select");
  const [markColour, setMarkColour] = useState(MARK_COLOURS[0]);
  const [tab, setTab] = useState<Tab>("summarise");
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [hits, setHits] = useState<SearchMatch[]>([]);
  const [hitIndex, setHitIndex] = useState(-1);
  const [redactPreview, setRedactPreview] = useState<RedactCandidate[]>([]);
  const [aiResult, setAiResult] = useState<AiSelectionResult | null>(null);
  // Restored from localStorage on mount: placed-but-not-yet-built form fields
  // are real work, and a refresh used to discard them silently.
  const [drafts, setDrafts] = useState<DraftField[]>(
    () => readDraft<DraftField[]>("formbuilder", documentId) ?? [],
  );
  const [placing, setPlacing] = useState<string | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved">("idle");

  // Small-screen drawers. On a wide viewport both panes are always visible and
  // these are inert; the media query is what turns them into overlays.
  const [railOpen, setRailOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const draft = useAutosaveDraft(
    "formbuilder", documentId, drafts, (d) => d.length === 0,
  );

  const canvasAreaRef = useRef<HTMLDivElement>(null);

  const activeGroup = TOOL_GROUPS.find(
    (group) => group.tools.some((tool) => tool.id === tab)) ?? TOOL_GROUPS[0];

  // ---------------------------------------------------------- loading

  const loadDocument = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [meta, buffer] = await Promise.all([
        api.document(documentId),
        api.downloadBuffer(documentId),
      ]);
      setDetail(meta);

      const task = pdfjs.getDocument({ data: buffer });
      const loaded = await task.promise;
      setPdf(loaded);
      setPages(await Promise.all(
        Array.from({ length: loaded.numPages }, (_, i) => loaded.getPage(i + 1)),
      ));
      setAnnotations(await api.annotations(documentId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => { loadDocument(); }, [loadDocument]);

  // Fit the first page to the available width when it would otherwise overflow.
  // Only ever narrows, and only once, so it never fights a zoom the user chose.
  //
  // useLayoutEffect, not useEffect: this must take effect before the browser
  // paints. A phone browser fixes its layout viewport from the first laid-out
  // frame, so a single painted frame at the overflowing width leaves the page
  // permanently zoomed out even after the scale is corrected.
  useLayoutEffect(() => {
    if (fittedRef.current || !pages.length) return;
    const area = canvasAreaRef.current;
    if (!area) return;

    const available = area.clientWidth - 32;      // padding either side
    const pageWidth = pages[0].getViewport({ scale: 1 }).width;
    if (available <= 0 || !pageWidth) return;

    fittedRef.current = true;
    if (pageWidth * scale > available) {
      // Round down to a hundredth so the page cannot land a pixel over.
      setScale(Math.max(Math.floor((available / pageWidth) * 100) / 100, 0.1));
    }
  }, [pages, scale]);

  /** Re-fetch after any operation that creates a new version. */
  const reload = useCallback(async (message?: string) => {
    if (message) notify(message, "ok");
    setSaving("saving");
    await loadDocument();
    setSaving("saved");
    setTimeout(() => setSaving("idle"), 1600);
  }, [loadDocument, notify]);

  // -------------------------------------------------------- selection

  useEffect(() => {
    function onUp() {
      if (tool !== "select") return;
      // Defer so the browser has committed the selection.
      window.setTimeout(() => setSelection(readSelection(scale)), 0);
    }
    // pointerup covers mouse and stylus. Touch selection is finished by the
    // platform's own selection handles, which raise selectionchange rather
    // than a pointer event, so both are needed to catch every device.
    document.addEventListener("pointerup", onUp);
    document.addEventListener("selectionchange", onUp);
    return () => {
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("selectionchange", onUp);
    };
  }, [scale, tool]);

  /** Turn a drawn gesture into a stored annotation.
   *
   * Marks are stored like every other annotation: alongside the document
   * rather than inside it, so drawing never rewrites the file. The Document
   * tab writes them in when a flattened copy is wanted.
   */
  async function addMark(
    pageNumber: number,
    drawn: DrawTool,
    result: { rect?: { x: number; y: number; width: number; height: number };
              points?: { x: number; y: number }[] },
  ) {
    const kind = drawn === "draw" ? "drawing" : drawn;
    let body: string | undefined;

    if (drawn === "textbox" || drawn === "note") {
      const typed = window.prompt(
        drawn === "note" ? "Note text" : "Text for the box");
      if (typed === null) return;          // cancelled
      body = typed;
    }

    try {
      // The server validates rectangles strictly — width and height must be
      // positive — so a zero-sized rect is rejected rather than stored. Paths
      // send no rect at all, and a note gets a pin-sized one at the point
      // that was clicked.
      const rect = result.points
        ? undefined
        : drawn === "note"
          ? { x: result.rect!.x, y: result.rect!.y, width: PIN, height: PIN }
          : result.rect;

      await api.createAnnotation(documentId, {
        kind: kind as Annotation["kind"],
        page: pageNumber,
        ...(rect ? { rect } : {}),
        points: result.points ?? [],
        colour: markColour,
        opacity: 1,
        body,
      });
      setAnnotations(await api.annotations(documentId));
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  async function addMarkup(kind: Annotation["kind"], colour: string, body?: string) {
    if (!selection) return;
    try {
      const created = await api.createAnnotation(documentId, {
        kind,
        page: selection.page,
        rect: selection.rects[0],
        quads: selection.rects,
        colour,
        selected_text: selection.text,
        body,
      });
      setAnnotations((previous) => [...previous, created]);
      window.getSelection()?.removeAllRanges();
      setSelection(null);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  async function comment() {
    const body = window.prompt("Comment");
    if (body?.trim()) await addMarkup("comment", "#FFD54F", body.trim());
  }

  async function copySelection() {
    if (!selection) return;
    try {
      await navigator.clipboard.writeText(selection.text);
      notify("Copied to clipboard", "ok");
    } catch {
      notify("The browser blocked clipboard access", "error");
    }
  }

  async function askAi(mode: "explain" | "summarize" | "translate") {
    if (!selection) return;
    const text = selection.text;

    let language: string | undefined;
    if (mode === "translate") {
      language = window.prompt("Translate into which language?", "French") ?? undefined;
      if (!language?.trim()) return;
    }

    setTab("ai");
    setAiResult(null);
    setSelection(null);
    notify(`Running ${mode}…`, "ok");

    try {
      setAiResult(await api.aiSelection(documentId, text, mode, language));
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  async function redactSelection() {
    if (!selection) return;
    if (!window.confirm(`Permanently remove "${selection.text.slice(0, 60)}"?`)) return;
    try {
      const result = await api.applyRedaction(documentId, [{
        kind: "manual",
        text: selection.text,
        page: selection.page,
        start: 0,
        end: selection.text.length,
        rects: selection.rects,
      }]);
      setSelection(null);
      await reload(result.note);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  // ------------------------------------------------------ page actions

  function jumpToPage(page: number) {
    const element = canvasAreaRef.current?.querySelector(`[data-page="${page}"]`);
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
    setCurrent(page);
  }

  async function runOperation(label: string, action: () => Promise<unknown>) {
    setSaving("saving");
    try {
      await action();
      setSelectedPages(new Set());
      await reload(label);
    } catch (e) {
      setSaving("idle");
      notify((e as Error).message, "error");
    }
  }

  async function snapshot(page: number, rect: { x: number; y: number; width: number; height: number }) {
    try {
      const blob = await api.snapshot(documentId, page, rect, 3);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `snapshot-p${page}.png`;
      link.click();
      URL.revokeObjectURL(url);
      notify("Snapshot saved", "ok");
      setTool("select");
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }

  // ------------------------------------------------------- shortcuts

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const meta = event.ctrlKey || event.metaKey;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(
        (event.target as HTMLElement)?.tagName,
      );
      if (typing) return;

      if (meta && event.key === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      } else if (meta && event.key === "f") {
        event.preventDefault();
        setTab("search");
      } else if (meta && event.key === "p") {
        event.preventDefault();
        window.print();
      } else if (!meta && event.key === "Escape") {
        setPaletteOpen(false);
        setTool("select");
        setSelection(null);
      } else if (!meta && (event.key === "PageDown" || event.key === "ArrowRight")) {
        jumpToPage(Math.min(current + 1, pages.length));
      } else if (!meta && (event.key === "PageUp" || event.key === "ArrowLeft")) {
        jumpToPage(Math.max(current - 1, 1));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, pages.length]);

  // --------------------------------------------------------- overlays

  const overlaysByPage = useMemo(() => {
    const map = new Map<number, Overlay[]>();
    const push = (page: number, items: Overlay[]) =>
      map.set(page, [...(map.get(page) ?? []), ...items]);

    for (const annotation of annotations) {
      push(annotation.page, annotationOverlays(annotation, () => {
        setTab("comments");
      }));
    }

    hits.forEach((hit, index) => {
      push(hit.page, hit.rects.map((rect, i) => ({
        key: `hit-${index}-${i}`,
        rect,
        className: `search-hit ${index === hitIndex ? "current" : ""}`,
      })));
    });

    drafts.forEach((draft) => {
      push(draft.page, [{
        key: draft.key,
        rect: { x: draft.x, y: draft.y, width: draft.width, height: draft.height },
        className: "sign-slot",
        title: `${draft.name} (${draft.type})`,
      }]);
    });

    redactPreview.forEach((candidate, index) => {
      push(candidate.page, candidate.rects.map((rect, i) => ({
        key: `redact-${index}-${i}`,
        rect,
        className: "redact-preview",
        title: `${candidate.kind}: ${candidate.text}`,
      })));
    });

    return map;
  }, [annotations, hits, hitIndex, redactPreview, drafts]);

  /** Arrow and freehand annotations, grouped by page for the SVG layer. */
  const strokesByPage = useMemo(() => {
    const grouped = new Map<number, Stroke[]>();
    for (const annotation of annotations) {
      for (const stroke of annotationStrokes(
        annotation, () => { setTab("comments"); })) {
        const list = grouped.get(annotation.page) ?? [];
        list.push(stroke);
        grouped.set(annotation.page, list);
      }
    }
    return grouped;
  }, [annotations]);

  // ------------------------------------------------------------ render

  if (loading) {
    return (
      <div className="body" style={{ placeItems: "center", display: "grid" }}>
        <div className="row"><span className="spinner" /> Loading document…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="body" style={{ placeItems: "center", display: "grid", padding: "2rem" }}>
        <div className="stack" style={{ maxWidth: 420 }}>
          <div className="error">{error}</div>
          <button className="btn" onClick={onBack}>Back to library</button>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="toolstrip">
        <button className="btn sm ghost" onClick={onBack}>← Library</button>
        <span className="divider" />

        <button className={`tool ${tool === "select" ? "active" : ""}`}
          onClick={() => setTool("select")}>Select</button>
        <button className={`tool ${tool === "snapshot" ? "active" : ""}`}
          onClick={() => setTool("snapshot")}
          title="Drag a region to capture it as an image">Snapshot</button>

        <span className="divider" />

        {/* Drawing tools. Each stays active until switched off, so several
            marks can be made without returning to the toolbar between them. */}
        {([
          ["shape", "Box", "Drag a rectangle"],
          ["arrow", "Arrow", "Drag from the note towards what it points at"],
          ["draw", "Draw", "Freehand"],
          ["textbox", "Text box", "Drag a box, then type the text"],
          ["note", "Note", "Click to drop a note pin"],
        ] as [DrawTool, string, string][]).map(([value, label, hint]) => (
          <button key={value} title={hint}
            className={`tool ${tool === value ? "active" : ""}`}
            onClick={() => setTool(tool === value ? "select" : value)}>
            {label}
          </button>
        ))}

        <span className="row" style={{ gap: 3 }} title="Colour for new marks">
          {MARK_COLOURS.map((colour) => (
            <button key={colour} aria-label={`Mark colour ${colour}`}
              onClick={() => setMarkColour(colour)}
              style={{
                width: 16, height: 16, borderRadius: "50%", background: colour,
                border: markColour === colour
                  ? "2px solid var(--ink)" : "1px solid var(--line)",
                cursor: "pointer", padding: 0,
              }} />
          ))}
        </span>

        <span className="divider" />

        {/* Page and stamping operations used to sit here as well as in the
            Edit group, where they have all their options. Two routes to the
            same thing, one of them cut down, is not a shortcut — it is a
            second place to keep correct. The strip now carries only what is
            about looking at the document. */}
        <button className="tool" onClick={() => { setTab("organise"); setPanelOpen(true); }}
          title="Rotate, delete, duplicate, extract, split, crop">
          Pages…
        </button>
        <button className="tool" onClick={() => { setTab("edit"); setPanelOpen(true); }}
          title="Watermark, page numbers, headers, compress, protect">
          Stamp…
        </button>

        <span className="divider" />

        <button className="tool" title="Print (Ctrl+P)" onClick={async () => {
          // Print the PDF itself, not the app chrome: open the bytes in a
          // hidden frame and print that.
          try {
            const blob = await api.downloadBlob(documentId);
            const url = URL.createObjectURL(blob);
            const frame = document.createElement("iframe");
            frame.style.position = "fixed";
            frame.style.right = "0";
            frame.style.bottom = "0";
            frame.style.width = "0";
            frame.style.height = "0";
            frame.style.border = "0";
            frame.src = url;
            frame.onload = () => {
              frame.contentWindow?.focus();
              frame.contentWindow?.print();
              setTimeout(() => {
                document.body.removeChild(frame);
                URL.revokeObjectURL(url);
              }, 60_000);
            };
            document.body.appendChild(frame);
          } catch (e) {
            notify((e as Error).message, "error");
          }
        }}>Print</button>

        <button className="tool" onClick={async () => {
          const blob = await api.downloadBlob(documentId);
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = detail?.filename ?? "document.pdf";
          link.click();
          URL.revokeObjectURL(url);
        }}>Download</button>

        <div style={{ flex: 1 }} />

        <div className="row">
          <button className="btn sm ghost" onClick={() =>
            setScale(ZOOMS[Math.max(ZOOMS.indexOf(scale) - 1, 0)])}>−</button>
          <span className="small muted" style={{ minWidth: 44, textAlign: "center" }}>
            {Math.round(scale * 100)}%
          </span>
          <button className="btn sm ghost" onClick={() =>
            setScale(ZOOMS[Math.min(ZOOMS.indexOf(scale) + 1, ZOOMS.length - 1)])}>+</button>

          <div className="drawer-toggles">
            <button
              className="btn sm ghost"
              aria-expanded={railOpen}
              onClick={() => { setRailOpen((v) => !v); setPanelOpen(false); }}
            >
              Pages
            </button>
            <button
              className="btn sm ghost"
              aria-expanded={panelOpen}
              onClick={() => { setPanelOpen((v) => !v); setRailOpen(false); }}
            >
              Tools
            </button>
          </div>
        </div>
      </div>

      <div className="body">
        {/* Only rendered as a real overlay by the small-screen media query;
            on a desktop it stays display:none and costs nothing. */}
        <div
          className={`drawer-backdrop ${railOpen || panelOpen ? "open" : ""}`}
          onClick={() => { setRailOpen(false); setPanelOpen(false); }}
        />

        <aside className={`rail ${railOpen ? "open" : ""}`}>
          <div className="rail-head">
            Pages {selectedPages.size > 0 && `· ${selectedPages.size} selected`}
          </div>
          {pages.map((_, index) => {
            const number = index + 1;
            return (
              <Thumbnail
                key={number}
                documentId={documentId}
                page={number}
                // Already decoded in this tab; no need to ask the server.
                proxy={pages[index]}
                active={current === number}
                selected={selectedPages.has(number)}
                dragging={dragFrom === number}
                dropTarget={dragOver === number && dragFrom !== number}
                version={detail?.version_count ?? 1}
                onDragStart={() => setDragFrom(number)}
                onDragOverPage={(over) => setDragOver(over)}
                onDrop={() => {
                  // The drop target is wherever the pointer finished, which is
                  // not necessarily this thumbnail — the gesture is owned by
                  // the one that was pressed.
                  const from = dragFrom;
                  const to = dragOver;
                  setDragFrom(null);
                  setDragOver(null);
                  if (!from || !to || from === to) return;

                  // Build the full new order: pull `from` out, insert at the
                  // drop position. The API requires every page exactly once.
                  const order = pages.map((_, i) => i + 1).filter((p) => p !== from);
                  order.splice(to - 1, 0, from);
                  runOperation("Pages reordered",
                    () => api.reorderPages(documentId, order));
                }}
                onDragEnd={() => { setDragFrom(null); setDragOver(null); }}
                onClick={(event) => {
                  if (event.shiftKey || event.ctrlKey || event.metaKey) {
                    const next = new Set(selectedPages);
                    next.has(number) ? next.delete(number) : next.add(number);
                    setSelectedPages(next);
                  } else {
                    setSelectedPages(new Set());
                    jumpToPage(number);
                  }
                }}
              />
            );
          })}
        </aside>

        <main className="canvas-area" ref={canvasAreaRef}>
          {pdf && pages.map((page, index) => (
            <PdfPage
              key={index}
              page={page}
              pageNumber={index + 1}
              scale={scale}
              overlays={overlaysByPage.get(index + 1) ?? []}
              strokes={strokesByPage.get(index + 1) ?? []}
              snapshotMode={tool === "snapshot" || placing !== null}
              drawTool={DRAW_TOOLS.includes(tool as DrawTool)
                ? (tool as DrawTool) : null}
              onDraw={addMark}
              onSnapshot={(pageNumber, rect) => {
                if (placing) {
                  setDrafts((current) =>
                    [...current, draftFromRect(placing, pageNumber, rect,
                                               current.length)]);
                  setPlacing(null);
                } else {
                  snapshot(pageNumber, rect);
                }
              }}
              onVisible={setCurrent}
            />
          ))}
        </main>

        <aside className={`panel ${panelOpen ? "open" : ""}`}>
          {/* Two levels rather than one. Twenty-two tools in a flat wrapping
              grid is a list to search through; grouped, it is a place to
              navigate, and only the handful you are using is ever on screen. */}
          <nav className="panel-groups" aria-label="Tool groups">
            {TOOL_GROUPS.map((group) => {
              const active = group.tools.some((t) => t.id === tab);
              return (
                <button key={group.id}
                  className={`panel-group ${active ? "active" : ""}`}
                  aria-current={active}
                  onClick={() => setTab(group.tools[0].id)}>
                  <span aria-hidden className="glyph">{group.glyph}</span>
                  {group.label}
                </button>
              );
            })}
          </nav>

          <div className="panel-tools" role="tablist"
               aria-label={activeGroup.label}>
            {activeGroup.tools.map((tool) => (
              <button key={tool.id} role="tab"
                aria-selected={tab === tool.id}
                className={tab === tool.id ? "active" : ""}
                title={tool.hint}
                onClick={() => setTab(tool.id)}>
                {tool.label}
              </button>
            ))}
          </div>

          <div className="panel-body">
            {tab === "text" && (
              <TextEditPanel
                documentId={documentId}
                pageCount={pages.length}
                currentPage={current}
                // Whatever is selected in the page is almost always what the
                // user means to change, so it prefills the field.
                selectedText={selection?.text}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "pagesfrom" && detail && (
              <PagesFromPanel
                documentId={documentId}
                workspaceId={detail.workspace_id}
                pageCount={pages.length}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "extras" && (
              <ExtrasPanel
                documentId={documentId}
                pageCount={pages.length}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "document" && (
              <DocumentPanel
                documentId={documentId}
                pageCount={pages.length}
                annotationCount={annotations.length}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "organise" && (
              <OrganisePanel
                documentId={documentId}
                pageCount={pages.length}
                selectedPages={[...selectedPages].sort((a, b) => a - b)}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "edit" && (
              <EditPanel
                documentId={documentId}
                onSaved={(message) => reload(message)}
                notify={notify}
              />
            )}

            {tab === "combine" && detail && (
              <CombinePanel
                documentId={documentId}
                workspaceId={detail.workspace_id}
                onCombined={(newId, message) => {
                  notify(message, "ok");
                  onOpenDocument?.(newId);
                }}
                notify={notify}
              />
            )}

            {tab === "summarise" && (
              <SummarisePanel
                documentId={documentId}
                onJumpToPage={jumpToPage}
                notify={notify}
              />
            )}

            {tab === "analyse" && (
              <AnalysePanel documentId={documentId} onJumpToPage={jumpToPage} />
            )}

            {tab === "builder" && (
              <FormBuilderPanel
                documentId={documentId}
                drafts={drafts}
                placing={placing}
                onStartPlacing={setPlacing}
                onStopPlacing={() => setPlacing(null)}
                onRemoveDraft={(key) =>
                  setDrafts((d) => d.filter((x) => x.key !== key))}
                onClearDrafts={() => {
                  // The fields are in the document now, so the stored draft
                  // must go too or it would reappear on the next visit.
                  setDrafts([]);
                  clearDraft("formbuilder", documentId);
                }}
                onUpdateDraft={(key, patch) =>
                  setDrafts((d) => d.map((x) =>
                    x.key === key ? { ...x, ...patch } : x))}
                notify={notify}
                onBuilt={(m) => reload(m)}
              />
            )}

            {tab === "versions" && (
              <VersionsPanel
                documentId={documentId}
                refreshKey={detail?.version_count ?? 0}
                notify={notify}
                onRestored={(m) => reload(m)}
              />
            )}

            {tab === "ai" && (
              <AiPanel
                documentId={documentId}
                onJumpToPage={jumpToPage}
                pending={aiResult}
                onDismissPending={() => setAiResult(null)}
              />
            )}

            {tab === "comments" && (
              <CommentsPanel
                annotations={annotations}
                onJump={(a) => jumpToPage(a.page)}
                onResolve={async (a) => {
                  await api.resolveAnnotation(documentId, a.id);
                  setAnnotations(await api.annotations(documentId));
                }}
                onReopen={async (a) => {
                  await api.reopenAnnotation(documentId, a.id);
                  setAnnotations(await api.annotations(documentId));
                }}
                onDelete={async (a) => {
                  await api.deleteAnnotation(documentId, a.id);
                  setAnnotations(await api.annotations(documentId));
                }}
              />
            )}

            {tab === "search" && (
              <SearchPanel
                documentId={documentId}
                pdf={pdf}
                onJump={(match, index, all) => {
                  setHits(all);
                  setHitIndex(index);
                  jumpToPage(match.page);
                }}
              />
            )}

            {tab === "security" && <SecurityPanel documentId={documentId} />}

            {tab === "form" && (
              <FormPanel documentId={documentId} onFilled={(m) => reload(m)} />
            )}

            {tab === "redact" && (
              <RedactPanel
                documentId={documentId}
                onPreview={setRedactPreview}
                onApplied={(m) => reload(m)}
              />
            )}

            {tab === "convert" && (
              <ConvertPanel documentId={documentId} notify={notify} />
            )}

            {tab === "sign" && (
              <SignPanel
                documentId={documentId}
                currentPage={current}
                notify={notify}
                onChanged={(m) => reload(m)}
              />
            )}

            {tab === "translate" && (
              <TranslatePanel
                documentId={documentId}
                pageCount={pages.length}
                onSaved={(m) => reload(m)}
              />
            )}

            {tab === "ocr" && <OcrPanel documentId={documentId} notify={notify} />}

            {tab === "compare" && detail && (
              <ComparePanel
                documentId={documentId}
                workspaceId={detail.workspace_id}
                notify={notify}
              />
            )}
          </div>
        </aside>
      </div>

      <div className="statusbar">
        <span>Page {current} of {pages.length}</span>
        <span>·</span>
        <span>{detail?.version_count ?? 1} version(s)</span>
        <span>·</span>
        <span>{annotations.length} annotation(s)</span>
        <div style={{ flex: 1 }} />
        {saving === "saving" && <span className="row"><span className="spinner" /> Saving…</span>}
        {saving === "saved" && <span style={{ color: "var(--ok)" }}>Saved</span>}
        {saving === "idle" && <DraftIndicator status={draft.status} />}
        {tool === "snapshot" && <span>Drag a region to capture</span>}
      </div>

      {paletteOpen && (
        <CommandPalette
          commands={ALL_TOOLS.map((tool) => ({
            id: tool.id, label: tool.label, group: tool.group, hint: tool.hint,
          }))}
          onRun={(id) => { setTab(id as Tab); setPanelOpen(true); }}
          onClose={() => setPaletteOpen(false)}
        />
      )}

      {selection && tool === "select" && (
        <SelectionToolbar
          selection={selection}
          onHighlight={(colour) => addMarkup("highlight", colour)}
          onUnderline={() => addMarkup("underline", "#1D4ED8")}
          onStrike={() => addMarkup("strikethrough", "#BE123C")}
          onComment={comment}
          onCopy={copySelection}
          onRedact={redactSelection}
          onAsk={askAi}
        />
      )}
    </>
  );
}

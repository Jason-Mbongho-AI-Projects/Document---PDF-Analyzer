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
import { PdfPage, annotationOverlays, type Overlay } from "../components/PdfPage";
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
import { TextEditPanel } from "../components/TextEditPanel";
import { clearDraft, readDraft, useAutosaveDraft } from "../useDraft";
import { DraftIndicator } from "../components/Panels";
import { FormBuilderPanel, VersionsPanel, draftFromRect,
  type DraftField } from "../components/FormBuilderPanel";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Tool = "select" | "snapshot";
type Tab = "summarise" | "analyse" | "ai" | "comments" | "search"
  | "security" | "form" | "builder" | "redact" | "convert" | "compare"
  | "sign" | "translate" | "ocr" | "versions"
  | "organise" | "edit" | "combine" | "text" | "pagesfrom" | "document";

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

  const draft = useAutosaveDraft(
    "formbuilder", documentId, drafts, (d) => d.length === 0,
  );

  const canvasAreaRef = useRef<HTMLDivElement>(null);

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

  const targetPages = selectedPages.size ? [...selectedPages].sort((a, b) => a - b) : [current];

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

      if (meta && event.key === "f") {
        event.preventDefault();
        setTab("search");
      } else if (meta && event.key === "p") {
        event.preventDefault();
        window.print();
      } else if (!meta && event.key === "Escape") {
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

        <button className="tool" onClick={() =>
          runOperation("Rotated", () => api.rotate(documentId, targetPages, 90))}>
          Rotate ⟳
        </button>
        <button className="tool" onClick={() =>
          runOperation("Rotated", () => api.rotate(documentId, targetPages, -90))}>
          Rotate ⟲
        </button>
        <button className="tool" onClick={() => {
          if (window.confirm(`Delete page(s) ${targetPages.join(", ")}?`)) {
            runOperation("Pages deleted", () => api.deletePages(documentId, targetPages));
          }
        }}>Delete pages</button>
        <button className="tool" onClick={() =>
          runOperation("Pages duplicated", () => api.duplicatePages(documentId, targetPages))}>
          Duplicate
        </button>
        <button className="tool" onClick={async () => {
          try {
            const blob = await api.extractPages(documentId, targetPages);
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `extract.pdf`;
            link.click();
            URL.revokeObjectURL(url);
          } catch (e) { notify((e as Error).message, "error"); }
        }}>Extract</button>

        <span className="divider" />

        <button className="tool" onClick={() => {
          const text = window.prompt("Watermark text", "CONFIDENTIAL");
          if (text?.trim()) {
            runOperation("Watermark added", () => api.watermark(documentId, text.trim()));
          }
        }}>Watermark</button>
        <button className="tool" onClick={() =>
          runOperation("Page numbers added",
            () => api.pageNumbers(documentId, "bottom-center"))}>
          Page numbers
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
              snapshotMode={tool === "snapshot" || placing !== null}
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
          <div className="panel-tabs">
            {/* Organise, Edit and Combine lead: they are what people look for
                first in a PDF tool, and they were the ones missing. */}
            {(["text", "organise", "pagesfrom", "edit", "combine", "document",
              "summarise", "analyse", "ai", "comments", "search", "security",
              "form", "builder", "redact", "convert", "compare", "sign",
              "translate", "ocr", "versions"] as Tab[])
              .map((name) => (
              <button key={name} className={tab === name ? "active" : ""}
                onClick={() => setTab(name)}>
                {name === "ai" ? "Ask AI" : name === "ocr" ? "OCR"
                  : name === "pagesfrom" ? "Insert pages"
                  : name === "document" ? "Document"
                  : name === "builder" ? "Form builder"
                  : name[0].toUpperCase() + name.slice(1)}
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

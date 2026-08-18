/**
 * Editing the words in a PDF.
 *
 * The panel is deliberately explicit about what it is doing, because PDF text
 * editing is not word processing and pretending otherwise produces nasty
 * surprises. Two facts drive the design:
 *
 *   * The replacement keeps the document's own font when the page allows it,
 *     and is drawn in a standard font when it does not — an embedded font is
 *     usually subsetted and cannot be given glyphs it does not carry. Which
 *     of the two happened is shown, not hidden.
 *
 *   * Text does not reflow. A longer replacement can overrun what follows, so
 *     the width is compared before saving and a warning is shown rather than
 *     silently producing an overlap.
 */
import { useState } from "react";
import { api } from "../api";

const FONTS = ["Helvetica", "Times", "Courier"];

interface Props {
  documentId: string;
  pageCount: number;
  currentPage: number;
  /** Text currently selected in the viewer, if any. */
  selectedText?: string;
  onSaved: (message: string) => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}

export function TextEditPanel({
  documentId, pageCount, currentPage, selectedText, onSaved, notify,
}: Props) {
  const [mode, setMode] = useState<"replace" | "add">("replace");

  // replace / delete
  const [find, setFind] = useState(selectedText ?? "");
  const [replace, setReplace] = useState("");
  const [page, setPage] = useState(currentPage);
  const [matches, setMatches] = useState<number | null>(null);

  // add
  const [addText, setAddText] = useState("");
  const [x, setX] = useState(72);
  const [y, setY] = useState(700);

  // shared formatting
  const [font, setFont] = useState("Helvetica");
  const [size, setSize] = useState<string>("");
  const [colour, setColour] = useState("#000000");
  const [bold, setBold] = useState(false);
  const [italic, setItalic] = useState(false);

  const [busy, setBusy] = useState(false);

  const style = {
    font,
    size: size ? Number(size) : null,
    colour,
    bold,
    italic,
  };

  async function check() {
    setBusy(true);
    try {
      const result = await api.findText(documentId, find, page);
      setMatches(result.count);
      if (result.count === 0) {
        notify("That text was not found on this page.", "error");
      }
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function run(replacement: string, label: string) {
    setBusy(true);
    try {
      const result = await api.editText(documentId, [
        { page, find, replace: replacement, style },
      ]);
      onSaved(result.note || label);
      setMatches(null);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <button className={`btn sm ${mode === "replace" ? "primary" : ""}`}
          onClick={() => setMode("replace")}>Change text</button>
        <button className={`btn sm ${mode === "add" ? "primary" : ""}`}
          onClick={() => setMode("add")}>Add text</button>
      </div>

      <label className="field">
        <span>Page</span>
        <input className="input" type="number" min={1} max={pageCount} value={page}
          onChange={(e) => setPage(Math.min(pageCount,
            Math.max(1, Number(e.target.value) || 1)))} />
      </label>

      {mode === "replace" ? (
        <>
          <label className="field">
            <span>Text to change</span>
            <textarea className="input" rows={2} value={find}
              placeholder="Type it exactly as it appears, or select it in the page"
              onChange={(e) => { setFind(e.target.value); setMatches(null); }} />
          </label>

          <div className="row">
            <button className="btn sm" disabled={busy || !find.trim()} onClick={check}>
              Find it
            </button>
            {matches !== null && (
              <span className="small muted">
                {matches} match{matches === 1 ? "" : "es"} on this page
              </span>
            )}
          </div>

          <label className="field" style={{ marginTop: 10 }}>
            <span>Replace with</span>
            <textarea className="input" rows={2} value={replace}
              placeholder="Leave empty to delete the text"
              onChange={(e) => setReplace(e.target.value)} />
          </label>

          <Formatting {...{ font, setFont, size, setSize, colour, setColour,
                            bold, setBold, italic, setItalic }} />

          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy || !find.trim() || !replace}
              onClick={() => run(replace, "Text replaced")}>
              {busy ? "Working…" : "Replace"}
            </button>
            <button className="btn danger" disabled={busy || !find.trim()}
              title="Remove this text from the document"
              onClick={() => run("", "Text deleted")}>
              Delete text
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="field">
            <span>Text to add</span>
            <textarea className="input" rows={2} value={addText}
              onChange={(e) => setAddText(e.target.value)} />
          </label>

          <div className="row">
            <label className="small" style={{ flex: 1 }}>
              From left (pt)
              <input className="input" type="number" value={x}
                onChange={(e) => setX(Number(e.target.value) || 0)} />
            </label>
            <label className="small" style={{ flex: 1 }}>
              From bottom (pt)
              <input className="input" type="number" value={y}
                onChange={(e) => setY(Number(e.target.value) || 0)} />
            </label>
          </div>
          <p className="small muted" style={{ marginTop: 4 }}>
            Measured from the bottom-left corner, as PDF coordinates are. 72
            points is one inch.
          </p>

          <Formatting {...{ font, setFont, size, setSize, colour, setColour,
                            bold, setBold, italic, setItalic }} />

          <button className="btn primary" style={{ marginTop: 10 }}
            disabled={busy || !addText.trim()}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await api.addText(
                  documentId, page, x, y, addText,
                  { font, size: size ? Number(size) : undefined,
                    colour, bold, italic });
                onSaved(result.note || "Text added");
                setAddText("");
              } catch (e) {
                notify((e as Error).message, "error");
              } finally {
                setBusy(false);
              }
            }}>
            {busy ? "Working…" : "Add text"}
          </button>
        </>
      )}

      <div className="notice" style={{ marginTop: 14 }}>
        <span className="small">
          Removed text is deleted from the file, not covered over. Where it
          can be done, the new text is written into the page's own text, so it
          keeps the original typeface and stays in the document's reading
          order. Where it cannot — an embedded font that cannot be given new
          glyphs, a replacement too wide to fit, or a font, size or colour
          chosen above — it is drawn over the page in the font shown instead.
          You are told which happened. Text does not reflow: a longer
          replacement may overlap what follows, and you will be told when that
          is likely.
        </span>
      </div>
    </>
  );
}

function Formatting({
  font, setFont, size, setSize, colour, setColour,
  bold, setBold, italic, setItalic,
}: {
  font: string; setFont: (v: string) => void;
  size: string; setSize: (v: string) => void;
  colour: string; setColour: (v: string) => void;
  bold: boolean; setBold: (v: boolean) => void;
  italic: boolean; setItalic: (v: boolean) => void;
}) {
  return (
    <>
      <div className="meta" style={{ marginTop: 12 }}>Formatting</div>
      <div className="row">
        <select className="input" value={font} onChange={(e) => setFont(e.target.value)}
                aria-label="Font">
          {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <input className="input" style={{ maxWidth: 92 }} type="number"
          value={size} placeholder="Size" aria-label="Font size"
          onChange={(e) => setSize(e.target.value)} />
        <input className="input" style={{ maxWidth: 52, padding: 2 }} type="color"
          value={colour} aria-label="Colour"
          onChange={(e) => setColour(e.target.value)} />
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <button className={`btn sm ${bold ? "primary" : ""}`}
          onClick={() => setBold(!bold)} aria-pressed={bold}>
          <strong>B</strong>
        </button>
        <button className={`btn sm ${italic ? "primary" : ""}`}
          onClick={() => setItalic(!italic)} aria-pressed={italic}>
          <em>I</em>
        </button>
        <span className="small muted">
          {size ? `${size}pt` : "matches the original size"}
        </span>
      </div>
    </>
  );
}

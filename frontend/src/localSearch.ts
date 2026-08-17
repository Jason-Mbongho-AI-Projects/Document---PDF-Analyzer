/**
 * Searching the document in the browser.
 *
 * The text and its geometry are already in this tab — PDF.js decoded them to
 * draw the selectable layer over every page. Asking the server to search the
 * same document again is a round trip for an answer we can compute locally,
 * and it makes a feature that needs no server depend on one.
 *
 * The result shape matches the API's, so the panel does not care which
 * answered. The server remains the fallback: it reads the stored bytes, so it
 * can still search a document this tab has not finished loading.
 */
import type { PDFDocumentProxy } from "pdfjs-dist";

export interface LocalMatch {
  page: number;
  start: number;
  end: number;
  text: string;
  context: string;
  rects: { x: number; y: number; width: number; height: number }[];
}

/** Characters either side of a hit, for the result list. */
const CONTEXT = 40;

/** Stop before a pathological query floods the panel and the overlay layer. */
const MAX_MATCHES = 500;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Find every occurrence of `query`, with a rectangle per hit.
 *
 * Rectangles come from the text items PDF.js reports, in view coordinates —
 * origin top-left, PDF points — which is what the overlay layer expects.
 */
export async function searchDocument(
  pdf: PDFDocumentProxy,
  query: string,
  { wholeWords = false }: { wholeWords?: boolean } = {},
): Promise<LocalMatch[]> {
  const needle = query.trim();
  if (!needle) return [];

  const pattern = new RegExp(
    wholeWords ? `\\b${escapeRegExp(needle)}\\b` : escapeRegExp(needle),
    "gi",
  );

  const matches: LocalMatch[] = [];

  for (let number = 1; number <= pdf.numPages; number++) {
    const page = await pdf.getPage(number);
    const viewport = page.getViewport({ scale: 1 });
    const content = await page.getTextContent();

    // One string per page, remembering where each item started, so a hit can
    // be mapped back to the items it covers.
    let pageText = "";
    const spans: { start: number; end: number; item: any }[] = [];

    for (const item of content.items as any[]) {
      if (typeof item.str !== "string") continue;
      const start = pageText.length;
      pageText += item.str;
      spans.push({ start, end: pageText.length, item });
      // PDF.js marks the end of a visual line; without a separator the last
      // word of one line and the first of the next merge into one token.
      if (item.hasEOL) pageText += "\n";
    }

    pattern.lastIndex = 0;
    let hit: RegExpExecArray | null;

    while ((hit = pattern.exec(pageText)) !== null) {
      const start = hit.index;
      const end = start + hit[0].length;

      const rects = spans
        .filter((span) => span.start < end && span.end > start)
        .map((span) => rectFor(span.item, viewport.height))
        .filter((rect): rect is NonNullable<typeof rect> => rect !== null);

      matches.push({
        page: number,
        start,
        end,
        text: hit[0],
        context: pageText
          .slice(Math.max(0, start - CONTEXT), end + CONTEXT)
          .replace(/\s+/g, " ")
          .trim(),
        rects,
      });

      if (matches.length >= MAX_MATCHES) return matches;
      // A zero-length match would spin here forever.
      if (hit[0].length === 0) pattern.lastIndex += 1;
    }
  }

  return matches;
}

/** A text item's box, converted from PDF space to view space. */
function rectFor(item: any, pageHeight: number) {
  const transform = item.transform;
  if (!Array.isArray(transform) || transform.length < 6) return null;

  const height = Math.hypot(transform[2], transform[3]) || item.height || 10;
  const width = item.width ?? 0;
  const x = transform[4];
  const baseline = transform[5];

  if (!width || !height) return null;

  return {
    x,
    // PDF measures from the bottom, the overlay layer from the top, and the
    // baseline sits above the descender.
    y: pageHeight - baseline - height,
    width,
    height,
  };
}

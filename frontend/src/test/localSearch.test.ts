/**
 * Searching in the browser.
 *
 * This replaced a server call, so the bar is that it behaves the same way:
 * the same matches, the same shape of result, and rectangles the overlay
 * layer can draw. The awkward parts are that PDF.js reports text in fragments
 * that do not respect word boundaries, and that its coordinates start at the
 * bottom of the page while the overlay measures from the top.
 */
import { describe, expect, it } from "vitest";

import { searchDocument } from "../localSearch";

/** A stand-in for PDF.js: text items with a transform, as it reports them. */
function fakePdf(pages: string[][], pageHeight = 800) {
  return {
    numPages: pages.length,
    getPage: async (number: number) => ({
      getViewport: () => ({ width: 600, height: pageHeight }),
      getTextContent: async () => ({
        items: pages[number - 1].map((str, index) => ({
          str,
          width: str.length * 6,
          height: 12,
          // [a, b, c, d, e, f] — e is x, f is the baseline in PDF space.
          transform: [12, 0, 0, 12, 50, pageHeight - 100 - index * 20],
          hasEOL: true,
        })),
      }),
    }),
  } as never;
}

describe("finding text", () => {
  it("finds a word and reports its page", async () => {
    const matches = await searchDocument(
      fakePdf([["The quick brown fox"], ["jumps over the lazy dog"]]), "lazy");

    expect(matches).toHaveLength(1);
    expect(matches[0].page).toBe(2);
    expect(matches[0].text).toBe("lazy");
  });

  it("is case insensitive", async () => {
    const matches = await searchDocument(fakePdf([["Quarterly Report"]]), "quarterly");
    expect(matches).toHaveLength(1);
  });

  it("finds every occurrence, not just the first", async () => {
    const matches = await searchDocument(
      fakePdf([["total total", "total again"]]), "total");
    expect(matches).toHaveLength(3);
  });

  it("returns nothing for text that is absent", async () => {
    expect(await searchDocument(fakePdf([["nothing here"]]), "absent")).toEqual([]);
  });

  it("ignores an empty query rather than matching everything", async () => {
    expect(await searchDocument(fakePdf([["anything"]]), "   ")).toEqual([]);
  });
});

describe("whole words", () => {
  it("excludes a match inside a longer word", async () => {
    const pdf = fakePdf([["the cat concatenate scatter"]]);

    expect(await searchDocument(pdf, "cat")).toHaveLength(3);
    expect(await searchDocument(pdf, "cat", { wholeWords: true })).toHaveLength(1);
  });
});

describe("result shape", () => {
  it("gives each match a rectangle to draw", async () => {
    const [match] = await searchDocument(fakePdf([["findable text"]]), "findable");

    expect(match.rects.length).toBeGreaterThan(0);
    expect(match.rects[0].width).toBeGreaterThan(0);
    expect(match.rects[0].height).toBeGreaterThan(0);
  });

  it("measures rectangles from the top, as the overlay layer does", async () => {
    // The item sits at baseline 700 on an 800pt page, so the overlay wants a
    // y near the top of the page, not near the bottom.
    const [match] = await searchDocument(fakePdf([["anchored"]], 800), "anchored");

    expect(match.rects[0].y).toBeGreaterThan(80);
    expect(match.rects[0].y).toBeLessThan(120);
  });

  it("includes surrounding context for the result list", async () => {
    const [match] = await searchDocument(
      fakePdf([["the target appears in a sentence"]]), "target");

    expect(match.context).toContain("target");
    expect(match.context.length).toBeGreaterThan("target".length);
  });
});

describe("awkward input", () => {
  it("treats regular expression characters as literal text", async () => {
    const matches = await searchDocument(
      fakePdf([["a price of $5.00 (net)"]]), "$5.00");

    expect(matches).toHaveLength(1);
  });

  it("does not merge the last word of a line with the first of the next", async () => {
    // Both fragments end a line, so "fox jumps" is not one token.
    const matches = await searchDocument(fakePdf([["fox", "jumps"]]), "foxjumps");
    expect(matches).toEqual([]);
  });

  it("stops rather than returning an unbounded number of hits", async () => {
    const many = Array.from({ length: 400 }, () => "aa aa aa");
    const matches = await searchDocument(fakePdf([many]), "aa");

    expect(matches.length).toBeLessThanOrEqual(500);
  });
});

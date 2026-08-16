"""
Cleaning up scanned pages: deskew, despeckle, contrast, binarise.

A scan that arrives tilted or speckled reads badly and OCRs worse, and OCR
accuracy is the real reason this exists — straightening a page before
recognition is usually worth more than any change of engine.

Every operation here rasterises the page and writes the cleaned image back,
so it is lossy by nature and only appropriate for scans. Applying it to a
document that already has a text layer would replace real text with a picture
of text, which is why the API refuses to do that without being told twice.

One honest limit: despeckling tells dirt from text by how much ink surrounds
each dark pixel. Where the print is so fine that its strokes are as thin as
the specks — tiny footnotes on a low-resolution scan — the two are not
distinguishable and some of that text will be thinned. Scanning at a higher
resolution is the fix; no filter can recover what the scan never separated.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from pypdf import PdfReader

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.render import render_page

# Angles beyond this are not skew, they are a page that was scanned sideways;
# rotating by a guess would make it worse.
MAX_SKEW = 15.0


@dataclass
class PageReport:
    page: int
    skew_corrected: float = 0.0
    actions: Sequence[str] = ()


def find_skew(image: Image.Image) -> float:
    """Estimate the tilt of the text, in degrees.

    Works by rotating candidate angles and measuring how sharply the ink
    piles up into rows: text that is level produces strong peaks in the
    row-sum profile, and text that is tilted smears them out. Coarse then
    fine, so the search stays cheap.
    """
    grey = ImageOps.grayscale(image)
    # Downscale: skew is a property of the layout, not of the detail, and the
    # search is quadratic in pixels.
    grey.thumbnail((800, 800))
    ink = 255 - np.asarray(grey, dtype=np.float32)
    ink -= ink.mean()

    def score(angle: float) -> float:
        if angle:
            rotated = np.asarray(
                Image.fromarray(ink).rotate(angle, resample=Image.BILINEAR,
                                            fillcolor=0),
                dtype=np.float32)
        else:
            rotated = ink
        profile = rotated.sum(axis=1)
        # Variance of the row profile: sharp rows of text score high.
        return float(np.var(profile))

    best, best_score = 0.0, score(0.0)
    for angle in np.arange(-MAX_SKEW, MAX_SKEW + 0.1, 1.0):
        value = score(float(angle))
        if value > best_score:
            best, best_score = float(angle), value

    # Refine around the winner.
    for angle in np.arange(best - 0.9, best + 0.95, 0.15):
        value = score(float(angle))
        if value > best_score:
            best, best_score = float(angle), value

    return round(best, 2)


def _despeckle(image: Image.Image, render_scale: float) -> tuple[Image.Image, int]:
    """Remove isolated dark specks, leaving strokes intact.

    A median filter cannot tell a speck from a letter: widen it enough to
    remove dirt at render resolution and it erodes the text as well. This
    counts how much ink surrounds each dark pixel instead. A speck sits alone
    and is cleared; a pixel inside a stroke has plenty of dark neighbours and
    is kept, so the glyphs come through untouched.
    """
    grey = ImageOps.grayscale(image)
    pixels = np.asarray(grey, dtype=np.uint8)

    # Generous threshold. Rendering antialiases a one-pixel speck into a
    # cluster of mid-greys, and a strict cut-off leaves those behind as visible
    # dirt. Text is safe regardless: what protects it is the neighbour count
    # below, not the threshold.
    dark = pixels < 215

    if not dark.any():
        return grey, 0

    # Window scaled to the render: a speck is one pixel in the original scan,
    # so it is about `render_scale` pixels across here.
    radius = max(1, int(round(render_scale)))
    window = 2 * radius + 1

    # Neighbour counts for every pixel at once, via an integral image.
    padded = np.pad(dark.astype(np.int32), radius + 1)
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    height, width = dark.shape
    y0, x0 = np.arange(height), np.arange(width)
    top = y0[:, None]
    left = x0[None, :]
    counts = (integral[top + window, left + window]
              - integral[top, left + window]
              - integral[top + window, left]
              + integral[top, left])

    # A stroke of width w contributes at least w*window dark pixels to the
    # window; an isolated speck contributes only itself and its own spread.
    threshold = max(2, (window * window) // 6)
    specks = dark & (counts <= threshold)

    if not specks.any():
        return grey, 0

    cleaned = pixels.copy()
    cleaned[specks] = 255
    return Image.fromarray(cleaned, mode="L"), int(specks.sum())


def clean_image(image: Image.Image, *, deskew: bool = True,
                despeckle: bool = True, contrast: bool = True,
                binarise: bool = False,
                render_scale: float = 1.0) -> tuple[Image.Image, PageReport]:
    """Apply the requested cleanups to one page image."""
    actions: List[str] = []
    angle = 0.0

    if deskew:
        angle = find_skew(image)
        if abs(angle) >= 0.2:
            image = image.rotate(angle, resample=Image.BICUBIC,
                                 expand=False, fillcolor="white")
            actions.append(f"deskewed {angle:+.2f}°")

    if despeckle:
        image, removed = _despeckle(image, render_scale)
        actions.append(f"despeckled ({removed} pixels of dirt)")

    if contrast:
        # cutoff=0 stretches between the actual darkest and lightest values.
        # A non-zero cutoff treats the extremes as outliers to be clipped,
        # which on a page of black text and white paper means crushing the
        # anti-aliased edges of every letter into a blob.
        image = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=0)
        actions.append("contrast normalised")

    if binarise:
        grey = ImageOps.grayscale(image)
        threshold = _otsu(np.asarray(grey))
        image = grey.point(lambda v: 255 if v > threshold else 0, mode="L")
        actions.append(f"binarised at {threshold}")

    return image, PageReport(page=0, skew_corrected=angle, actions=actions)


def _otsu(pixels: np.ndarray) -> int:
    """Pick a black/white threshold from the image's own histogram."""
    histogram = np.bincount(pixels.ravel(), minlength=256).astype(np.float64)
    total = histogram.sum()
    if total == 0:
        return 128

    weights = np.cumsum(histogram)
    means = np.cumsum(histogram * np.arange(256))
    overall = means[-1] / total

    # Between-class variance for every candidate threshold at once.
    with np.errstate(divide="ignore", invalid="ignore"):
        background = weights / total
        foreground = 1.0 - background
        mean_bg = np.divide(means, weights, out=np.zeros(256), where=weights > 0)
        variance = background * foreground * (mean_bg - overall) ** 2

    return int(np.nanargmax(variance))


def enhance(data: bytes, *, pages: Optional[Sequence[int]] = None,
            deskew: bool = True, despeckle: bool = True,
            contrast: bool = True, binarise: bool = False,
            dpi: int = 200) -> tuple[bytes, List[dict]]:
    """Rebuild the document from cleaned page images.

    Returns the new PDF and a per-page report of what was done, because
    "enhanced" on its own tells the user nothing about whether it helped.
    """
    if not any((deskew, despeckle, contrast, binarise)):
        raise PDFEngineError("No enhancement was requested.")

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    if total == 0:
        raise PDFEngineError("This document has no pages.")

    targets = set(pages or range(1, total + 1))
    outside = [p for p in targets if p < 1 or p > total]
    if outside:
        raise PDFEngineError(
            f"Page(s) {', '.join(map(str, sorted(outside)))} do not exist; the "
            f"document has {total}."
        )

    scale = max(dpi / 72.0, 1.0)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    report: List[dict] = []

    for number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        pdf.setPageSize((width, height))

        rendered = Image.open(io.BytesIO(
            render_page(data, number, scale=scale, fmt="png").data))

        if number in targets:
            cleaned, page_report = clean_image(
                rendered, deskew=deskew, despeckle=despeckle,
                contrast=contrast, binarise=binarise, render_scale=scale)
            report.append({"page": number,
                           "skew_corrected": page_report.skew_corrected,
                           "actions": list(page_report.actions)})
        else:
            cleaned = rendered
            report.append({"page": number, "skew_corrected": 0.0,
                           "actions": ["left unchanged"]})

        pdf.drawImage(ImageReader(cleaned), 0, 0, width=width, height=height)
        pdf.showPage()

    pdf.save()
    return buffer.getvalue(), report

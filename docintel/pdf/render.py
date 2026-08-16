"""
Page rasterisation via PDFium (pypdfium2, BSD-3-Clause/Apache-2.0).

Used for page thumbnails and for the snapshot tool. Snapshot captures a
region of the *document*, rendered server-side at the requested resolution —
not a screenshot of the browser, so the output is clean and as sharp as the
caller asks for.

Rendering is memory-bounded on purpose: one page is rasterised at a time and
the requested scale is capped, so a 1,000-page document or an absurd zoom
cannot exhaust the worker.
"""
import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pypdfium2 as pdfium

from docintel.pdf.engine import PDFEngineError, PasswordRequired

MAX_SCALE = 8.0
MAX_PIXELS = 40_000_000        # ~40 MP ceiling per rendered page


@dataclass
class RenderedImage:
    page: int
    width: int
    height: int
    fmt: str
    data: bytes


def _open(data: bytes, password: Optional[str] = None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data, password=password)
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message:
            raise PasswordRequired("This PDF is password protected.") from exc
        raise PDFEngineError(f"The document could not be rendered: {exc}") from exc
    except Exception as exc:
        raise PDFEngineError(f"The document could not be rendered: {exc}") from exc


def _encode(image, fmt: str, quality: int = 90) -> bytes:
    fmt = fmt.lower()
    if fmt not in ("png", "jpg", "jpeg", "webp"):
        raise PDFEngineError(f"Unsupported image format '{fmt}'.")

    pillow_format = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt]
    if pillow_format == "JPEG" and image.mode in ("RGBA", "P"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    if pillow_format == "JPEG":
        image.save(buffer, pillow_format, quality=quality, optimize=True)
    else:
        image.save(buffer, pillow_format)
    return buffer.getvalue()


def _safe_scale(requested: float, width_pt: float, height_pt: float) -> float:
    scale = max(0.05, min(float(requested), MAX_SCALE))
    pixels = (width_pt * scale) * (height_pt * scale)
    if pixels > MAX_PIXELS:
        # Shrink to fit the ceiling rather than refusing outright.
        scale *= (MAX_PIXELS / pixels) ** 0.5
    return scale


def render_page(
    data: bytes, page_number: int, scale: float = 1.5,
    fmt: str = "png", password: Optional[str] = None,
) -> RenderedImage:
    pdf = _open(data, password)
    try:
        total = len(pdf)
        if page_number < 1 or page_number > total:
            raise PDFEngineError(
                f"Page {page_number} is out of range; the document has {total} page(s)."
            )

        page = pdf[page_number - 1]
        effective = _safe_scale(scale, page.get_width(), page.get_height())
        image = page.render(scale=effective).to_pil()
        return RenderedImage(page_number, image.width, image.height, fmt,
                             _encode(image, fmt))
    finally:
        pdf.close()


def render_thumbnails(
    data: bytes, pages: Optional[List[int]] = None,
    max_edge: int = 220, fmt: str = "png", password: Optional[str] = None,
) -> List[RenderedImage]:
    """Small previews for a page organiser grid.

    Pages are rendered one at a time and released immediately, so thumbnailing
    a large document has flat memory use.
    """
    pdf = _open(data, password)
    try:
        total = len(pdf)
        targets = pages or list(range(1, total + 1))

        results: List[RenderedImage] = []
        for number in targets:
            if number < 1 or number > total:
                raise PDFEngineError(f"Page {number} is out of range (1..{total}).")

            page = pdf[number - 1]
            longest = max(page.get_width(), page.get_height()) or 1
            image = page.render(scale=max_edge / longest).to_pil()
            results.append(RenderedImage(number, image.width, image.height, fmt,
                                         _encode(image, fmt)))
        return results
    finally:
        pdf.close()


def render_region(
    data: bytes, page_number: int, box: Tuple[float, float, float, float],
    scale: float = 2.0, fmt: str = "png", password: Optional[str] = None,
) -> RenderedImage:
    """Snapshot: capture one rectangular region of a page.

    `box` is (left, top, right, bottom) in PDF points with the origin at the
    page's top-left, which is what a viewer's selection rectangle produces.
    """
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        raise PDFEngineError("Snapshot region must have positive width and height.")

    pdf = _open(data, password)
    try:
        total = len(pdf)
        if page_number < 1 or page_number > total:
            raise PDFEngineError(f"Page {page_number} is out of range (1..{total}).")

        page = pdf[page_number - 1]
        page_width, page_height = page.get_width(), page.get_height()

        # Clamp so a selection dragged past the page edge still works.
        left = max(0.0, min(left, page_width))
        right = max(0.0, min(right, page_width))
        top = max(0.0, min(top, page_height))
        bottom = max(0.0, min(bottom, page_height))
        if right - left < 1 or bottom - top < 1:
            raise PDFEngineError("Snapshot region is too small.")

        effective = _safe_scale(scale, page_width, page_height)
        image = page.render(scale=effective).to_pil()

        crop = (
            int(left * effective), int(top * effective),
            int(right * effective), int(bottom * effective),
        )
        region = image.crop(crop)
        return RenderedImage(page_number, region.width, region.height, fmt,
                             _encode(region, fmt))
    finally:
        pdf.close()

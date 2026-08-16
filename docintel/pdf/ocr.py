"""
OCR provider abstraction.

No OCR engine is bundled. Tesseract and OCRmyPDF are external binaries with
their own install and licensing story, and shipping a silently-degraded
"OCR" that returns nothing would be worse than saying plainly that it is not
configured.

So this module does three real things:

  * detects which pages actually need OCR, which is useful on its own and
    needs no engine at all;
  * exposes a provider interface with a Tesseract implementation that works
    the moment the binary is installed;
  * reports unavailability with the reason and the install command, rather
    than failing obscurely at request time.
"""
import io
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.render import render_page
from docintel.pdf.text import extract

# A page with fewer extractable characters than this is treated as having no
# usable text layer.
TEXT_THRESHOLD = 20

INSTALL_HINT = (
    "No OCR engine is installed on this server. Install Tesseract "
    "(https://github.com/tesseract-ocr/tesseract) and the pytesseract package, "
    "then restart to enable OCR."
)


@dataclass
class PageNeed:
    page: int
    characters: int
    needs_ocr: bool
    reason: str


@dataclass
class OcrAssessment:
    pages: List[PageNeed] = field(default_factory=list)

    @property
    def pages_needing_ocr(self) -> List[int]:
        return [p.page for p in self.pages if p.needs_ocr]

    @property
    def classification(self) -> str:
        if not self.pages:
            return "empty"
        needing = len(self.pages_needing_ocr)
        if needing == 0:
            return "native"
        if needing == len(self.pages):
            return "no_text_layer"
        return "mixed"

    @property
    def summary(self) -> str:
        mapping = {
            "native": "Every page has an extractable text layer; OCR is not needed.",
            "no_text_layer": (
                "No page has an extractable text layer. This is typical of a "
                "scanned document and OCR would be required to read it."
            ),
            "mixed": (
                f"{len(self.pages_needing_ocr)} of {len(self.pages)} pages have "
                "no extractable text. The document mixes digital and scanned "
                "content."
            ),
            "empty": "This document has no pages.",
        }
        return mapping[self.classification]


def _words_from(report: Dict[str, list]) -> List[Dict[str, object]]:
    """Pull word boxes out of a pytesseract data report.

    Entries with no text, or with a negative confidence, are layout rows
    rather than words and would otherwise become empty boxes in the text
    layer.
    """
    words: List[Dict[str, object]] = []
    texts = report.get("text", [])

    for index, raw in enumerate(texts):
        word = str(raw).strip()
        if not word:
            continue
        try:
            confidence = float(report["conf"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            confidence = -1.0
        if confidence < 0:
            continue
        try:
            words.append({
                "text": word,
                "left": int(report["left"][index]),
                "top": int(report["top"][index]),
                "width": int(report["width"][index]),
                "height": int(report["height"][index]),
                "conf": confidence,
            })
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return words


@dataclass
class OcrResult:
    pages: List[Dict[str, object]]
    engine: str
    language: str
    mean_confidence: Optional[float] = None

    @property
    def text(self) -> str:
        return "\n\n".join(str(p.get("text", "")) for p in self.pages)


def assess(data: bytes) -> OcrAssessment:
    """Work out which pages would need OCR. Requires no OCR engine."""
    assessment = OcrAssessment()
    for page in extract(data):
        characters = len(page.text.strip())
        needs = characters < TEXT_THRESHOLD
        assessment.pages.append(PageNeed(
            page=page.page,
            characters=characters,
            needs_ocr=needs,
            reason=(
                "No extractable text; the page is probably an image."
                if needs else
                f"{characters} characters of extractable text."
            ),
        ))
    return assessment


class OCRProvider(ABC):
    name = "abstract"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @property
    @abstractmethod
    def reason(self) -> Optional[str]: ...

    @abstractmethod
    def languages(self) -> List[str]: ...

    @abstractmethod
    def recognise(self, data: bytes, pages: Optional[List[int]] = None,
                  language: str = "eng", scale: float = 3.0,
                  progress: Optional[Callable[[float, str], None]] = None) -> OcrResult: ...


class TesseractProvider(OCRProvider):
    """Works as soon as the Tesseract binary and pytesseract are present."""
    name = "tesseract"

    def __init__(self):
        self._binary = shutil.which("tesseract") or self._windows_path()

    @staticmethod
    def _windows_path() -> Optional[str]:
        import os
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.exists(candidate):
                return candidate
        return None

    @property
    def _module(self):
        try:
            import pytesseract
            return pytesseract
        except ImportError:
            return None

    @property
    def available(self) -> bool:
        return bool(self._binary) and self._module is not None

    @property
    def reason(self) -> Optional[str]:
        if self.available:
            return None
        if not self._binary:
            return INSTALL_HINT
        return (
            "The Tesseract binary is installed but the pytesseract package is "
            "missing. Run: pip install pytesseract"
        )

    def languages(self) -> List[str]:
        if not self.available:
            return []
        try:
            output = subprocess.run(
                [self._binary, "--list-langs"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            return [line.strip() for line in output.splitlines()[1:] if line.strip()]
        except Exception:
            return ["eng"]

    def recognise(self, data, pages=None, language="eng", scale=3.0,
                  progress=None) -> OcrResult:
        if not self.available:
            raise PDFEngineError(self.reason or INSTALL_HINT)

        pytesseract = self._module
        pytesseract.pytesseract.tesseract_cmd = self._binary

        from PIL import Image

        assessment = assess(data)
        targets = pages or [p.page for p in assessment.pages]

        results: List[Dict[str, object]] = []
        confidences: List[float] = []

        for index, number in enumerate(targets, start=1):
            if progress:
                progress(index / max(len(targets), 1),
                         f"Reading page {number} of {len(targets)}")

            image = Image.open(io.BytesIO(
                render_page(data, number, scale=scale, fmt="png").data
            ))

            try:
                text = pytesseract.image_to_string(image, lang=language)
                report = pytesseract.image_to_data(
                    image, lang=language, output_type=pytesseract.Output.DICT,
                )
                scores = [int(c) for c in report.get("conf", []) if str(c).lstrip("-").isdigit()]
                scores = [s for s in scores if s >= 0]
                page_confidence = sum(scores) / len(scores) if scores else None
                words = _words_from(report)
            except Exception as exc:
                raise PDFEngineError(f"OCR failed on page {number}: {exc}") from exc

            if page_confidence is not None:
                confidences.append(page_confidence)

            results.append({
                "page": number,
                "text": text.strip(),
                "confidence": round(page_confidence, 1) if page_confidence else None,
                "characters": len(text.strip()),
                # Word geometry, in pixels of the rendered image. Kept so the
                # recognised text can be written back as a real, selectable
                # text layer; without positions all that can be offered is a
                # transcript in a side panel.
                "words": words,
                "image_size": [image.width, image.height],
            })

        return OcrResult(
            pages=results,
            engine=self.name,
            language=language,
            mean_confidence=(
                round(sum(confidences) / len(confidences), 1) if confidences else None
            ),
        )


class UnavailableProvider(OCRProvider):
    """Stands in when nothing is installed, so callers get a clear reason."""
    name = "none"

    @property
    def available(self) -> bool:
        return False

    @property
    def reason(self) -> Optional[str]:
        return INSTALL_HINT

    def languages(self) -> List[str]:
        return []

    def recognise(self, data, pages=None, language="eng", scale=3.0, progress=None):
        raise PDFEngineError(INSTALL_HINT)


_provider: Optional[OCRProvider] = None


def get_provider() -> OCRProvider:
    global _provider
    if _provider is None:
        tesseract = TesseractProvider()
        _provider = tesseract if tesseract.available else UnavailableProvider()
    return _provider


def set_provider(provider: Optional[OCRProvider]) -> None:
    global _provider
    _provider = provider

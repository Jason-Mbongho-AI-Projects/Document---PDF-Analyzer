"""
PDF engine abstraction.

Every low-level PDF manipulation goes through this interface so the
underlying library can be swapped without touching services, jobs or the API.

Library choice is deliberate and licence-driven:

  pypdf      BSD-3-Clause  page geometry, composition, forms
  pikepdf    MPL-2.0       encryption, linearisation, object-level compression
  pypdfium2  BSD-3/Apache  rasterisation (PDFium, the engine in Chrome)
  reportlab  BSD           generated overlays and new documents
  Pillow     MIT-CMU       image handling

PyMuPDF/fitz is deliberately NOT used. It is AGPL-3.0, which obliges you to
publish the source of a networked application that links it, or to buy a
commercial licence from Artifex. Every capability it would have provided here
is covered by the permissive set above.

Operations are pure: bytes in, bytes out. No operation mutates its input, and
persistence/versioning is the caller's concern.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


class PDFEngineError(RuntimeError):
    """Raised when a PDF cannot be processed. Message is safe to surface."""


class PasswordRequired(PDFEngineError):
    pass


@dataclass
class PageGeometry:
    number: int
    width: float
    height: float
    rotation: int

    @property
    def orientation(self) -> str:
        # Rotation swaps the visual aspect.
        w, h = (self.height, self.width) if self.rotation % 180 else (self.width, self.height)
        return "landscape" if w > h else "portrait"


@dataclass
class CompressionResult:
    original_bytes: int
    compressed_bytes: int
    data: bytes

    @property
    def reduction_percent(self) -> float:
        """Measured, never estimated."""
        if self.original_bytes <= 0:
            return 0.0
        saved = self.original_bytes - self.compressed_bytes
        return round(max(saved, 0) / self.original_bytes * 100, 1)


class PDFEngine(ABC):
    """Low-level PDF operations."""

    # --- inspection ---------------------------------------------------
    @abstractmethod
    def page_count(self, data: bytes) -> int: ...

    @abstractmethod
    def geometry(self, data: bytes) -> List[PageGeometry]: ...

    @abstractmethod
    def is_encrypted(self, data: bytes) -> bool: ...

    # --- page organisation --------------------------------------------
    @abstractmethod
    def reorder(self, data: bytes, order: Sequence[int]) -> bytes: ...

    @abstractmethod
    def rotate(self, data: bytes, pages: Sequence[int], degrees: int) -> bytes: ...

    @abstractmethod
    def delete_pages(self, data: bytes, pages: Sequence[int]) -> bytes: ...

    @abstractmethod
    def extract_pages(self, data: bytes, pages: Sequence[int]) -> bytes: ...

    @abstractmethod
    def duplicate_pages(self, data: bytes, pages: Sequence[int]) -> bytes: ...

    @abstractmethod
    def insert(self, data: bytes, other: bytes, at: int) -> bytes: ...

    @abstractmethod
    def crop(self, data: bytes, pages: Sequence[int],
             box: Tuple[float, float, float, float]) -> bytes: ...

    @abstractmethod
    def merge(self, documents: Sequence[bytes]) -> bytes: ...

    @abstractmethod
    def split_ranges(self, data: bytes,
                     ranges: Sequence[Tuple[int, int]]) -> List[bytes]: ...

    # --- composition ---------------------------------------------------
    @abstractmethod
    def watermark_text(self, data: bytes, text: str, **options) -> bytes: ...

    @abstractmethod
    def page_numbers(self, data: bytes, **options) -> bytes: ...

    @abstractmethod
    def header_footer(self, data: bytes, header: str = "",
                      footer: str = "", **options) -> bytes: ...

    @abstractmethod
    def blank_document(self, pages: int, size: str,
                       orientation: str) -> bytes: ...

    # --- security and size ---------------------------------------------
    @abstractmethod
    def protect(self, data: bytes, user_password: str,
                owner_password: Optional[str] = None, **permissions) -> bytes: ...

    @abstractmethod
    def unlock(self, data: bytes, password: str) -> bytes: ...

    @abstractmethod
    def compress(self, data: bytes, preset: str) -> CompressionResult: ...


def get_engine() -> PDFEngine:
    from docintel.pdf.operations import PyPdfEngine
    return PyPdfEngine()

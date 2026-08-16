"""
PDF engine tests.

Every output is re-opened and inspected. A test never passes just because an
operation returned bytes — the result must be a valid PDF with the expected
structure.
"""
import io

import pytest
from pypdf import PdfReader

import pdf_corpus as corpus
from docintel.pdf.engine import PDFEngineError, PasswordRequired, get_engine

engine = get_engine()


def reopen(data: bytes, password=None) -> PdfReader:
    """Proof the output is a real, parseable PDF."""
    reader = PdfReader(io.BytesIO(data))
    if password:
        assert reader.decrypt(password) != 0
    return reader


# ------------------------------------------------------------ inspection

def test_page_count_and_geometry():
    data = corpus.multipage_pdf(4)
    assert engine.page_count(data) == 4

    geometry = engine.geometry(data)
    assert len(geometry) == 4
    assert geometry[0].width == 612.0 and geometry[0].height == 792.0
    assert geometry[0].orientation == "portrait"


def test_corrupt_pdf_raises_a_clean_error():
    with pytest.raises(PDFEngineError):
        engine.page_count(corpus.corrupt_pdf())


# --------------------------------------------------------- organisation

def test_reorder_reverses_pages():
    data = corpus.multipage_pdf(4)
    out = engine.reorder(data, [4, 3, 2, 1])
    assert len(reopen(out).pages) == 4


def test_reorder_must_include_every_page():
    with pytest.raises(PDFEngineError, match="every page"):
        engine.reorder(corpus.multipage_pdf(4), [1, 2])


def test_rotate_sets_the_rotation_key():
    out = engine.rotate(corpus.multipage_pdf(3), [2], 90)
    reader = reopen(out)
    assert reader.pages[1].get("/Rotate") == 90
    assert reader.pages[0].get("/Rotate", 0) == 0


def test_rotate_rejects_non_multiples_of_90():
    with pytest.raises(PDFEngineError, match="multiple of 90"):
        engine.rotate(corpus.multipage_pdf(2), [1], 45)


def test_delete_pages_removes_them():
    out = engine.delete_pages(corpus.multipage_pdf(5), [2, 4])
    assert len(reopen(out).pages) == 3


def test_cannot_delete_every_page():
    with pytest.raises(PDFEngineError, match="at least one page"):
        engine.delete_pages(corpus.multipage_pdf(2), [1, 2])


def test_extract_pages_preserves_the_source():
    source = corpus.multipage_pdf(6)
    out = engine.extract_pages(source, [2, 3, 4])
    assert len(reopen(out).pages) == 3
    assert engine.page_count(source) == 6      # untouched


def test_duplicate_pages():
    out = engine.duplicate_pages(corpus.multipage_pdf(3), [1, 3])
    assert len(reopen(out).pages) == 5


@pytest.mark.parametrize("at,expected", [(1, 4), (2, 4), (4, 4)])
def test_insert_at_positions(at, expected):
    out = engine.insert(corpus.multipage_pdf(3), corpus.clean_pdf(), at)
    assert len(reopen(out).pages) == expected


def test_insert_out_of_range_is_rejected():
    with pytest.raises(PDFEngineError, match="out of range"):
        engine.insert(corpus.multipage_pdf(3), corpus.clean_pdf(), 99)


def test_merge_concatenates_in_order():
    out = engine.merge([corpus.multipage_pdf(2), corpus.multipage_pdf(3)])
    assert len(reopen(out).pages) == 5


def test_merge_reports_which_document_failed():
    with pytest.raises(PDFEngineError, match="Document 2"):
        engine.merge([corpus.clean_pdf(), corpus.corrupt_pdf()])


def test_split_produces_independent_documents():
    parts = engine.split_ranges(corpus.multipage_pdf(6), [(1, 2), (3, 6)])
    assert [len(reopen(p).pages) for p in parts] == [2, 4]


def test_split_rejects_an_invalid_range():
    with pytest.raises(PDFEngineError, match="invalid"):
        engine.split_ranges(corpus.multipage_pdf(3), [(2, 9)])


def test_crop_shrinks_the_mediabox():
    out = engine.crop(corpus.multipage_pdf(2), [1], (100, 100, 400, 600))
    page = reopen(out).pages[0]
    assert float(page.mediabox.width) == 300.0
    assert float(page.mediabox.height) == 500.0


def test_crop_cannot_enlarge_a_page():
    """A box larger than the page is clamped, not honoured."""
    out = engine.crop(corpus.multipage_pdf(1), [1], (-500, -500, 5000, 5000))
    page = reopen(out).pages[0]
    assert float(page.mediabox.width) == 612.0
    assert float(page.mediabox.height) == 792.0


def test_crop_rejects_an_inverted_box():
    with pytest.raises(PDFEngineError, match="positive width"):
        engine.crop(corpus.multipage_pdf(1), [1], (400, 400, 100, 100))


def test_out_of_range_page_is_rejected():
    with pytest.raises(PDFEngineError, match="out of range"):
        engine.rotate(corpus.multipage_pdf(2), [9], 90)


# ---------------------------------------------------------- composition

def test_watermark_keeps_page_count_and_reopens():
    out = engine.watermark_text(corpus.multipage_pdf(3), "CONFIDENTIAL")
    assert len(reopen(out).pages) == 3


def test_watermark_can_target_specific_pages():
    out = engine.watermark_text(corpus.multipage_pdf(4), "DRAFT", pages=[2])
    assert len(reopen(out).pages) == 4


def test_empty_watermark_is_rejected():
    with pytest.raises(PDFEngineError, match="cannot be empty"):
        engine.watermark_text(corpus.multipage_pdf(1), "   ")


def test_page_numbers_render_and_are_extractable():
    out = engine.page_numbers(corpus.multipage_pdf(3), position="bottom-center")
    reader = reopen(out)
    text = reader.pages[2].extract_text() or ""
    assert "3" in text


def test_page_numbers_honour_start_at():
    out = engine.page_numbers(corpus.multipage_pdf(2), start_at=10)
    assert "10" in (reopen(out).pages[0].extract_text() or "")


def test_page_numbers_reject_a_bad_position():
    with pytest.raises(PDFEngineError, match="Unknown position"):
        engine.page_numbers(corpus.multipage_pdf(1), position="middle-nowhere")


def test_page_number_format_is_validated():
    with pytest.raises(PDFEngineError, match="may only use"):
        engine.page_numbers(corpus.multipage_pdf(1), format="{evil}")


def test_header_and_footer_text_appears():
    out = engine.header_footer(corpus.multipage_pdf(2),
                               header="Acme Corp", footer="Confidential")
    text = reopen(out).pages[0].extract_text() or ""
    assert "Acme Corp" in text and "Confidential" in text


def test_header_footer_requires_some_text():
    with pytest.raises(PDFEngineError, match="Provide header"):
        engine.header_footer(corpus.multipage_pdf(1))


@pytest.mark.parametrize("size,orientation,width", [
    ("letter", "portrait", 612.0),
    ("a4", "portrait", 595.28),
    ("letter", "landscape", 792.0),
])
def test_blank_document_sizes(size, orientation, width):
    out = engine.blank_document(2, size, orientation)
    reader = reopen(out)
    assert len(reader.pages) == 2
    assert round(float(reader.pages[0].mediabox.width), 2) == width


def test_blank_document_rejects_unknown_size():
    with pytest.raises(PDFEngineError, match="Unknown page size"):
        engine.blank_document(1, "tabloid-xl", "portrait")


# ------------------------------------------------------------- security

def test_protect_then_open_with_password():
    protected = engine.protect(corpus.multipage_pdf(3), "open-sesame")
    assert engine.is_encrypted(protected)

    reader = reopen(protected, password="open-sesame")
    assert len(reader.pages) == 3


def test_protected_document_cannot_be_read_without_the_password():
    protected = engine.protect(corpus.multipage_pdf(2), "open-sesame")
    with pytest.raises(PasswordRequired):
        engine.page_count(protected)


def test_unlock_removes_encryption():
    protected = engine.protect(corpus.multipage_pdf(3), "open-sesame")
    unlocked = engine.unlock(protected, "open-sesame")

    assert not engine.is_encrypted(unlocked)
    assert engine.page_count(unlocked) == 3


def test_unlock_requires_the_correct_password():
    """No cracking, no bypass: the wrong password fails."""
    protected = engine.protect(corpus.multipage_pdf(2), "open-sesame")
    with pytest.raises(PasswordRequired):
        engine.unlock(protected, "guessing")
    with pytest.raises(PasswordRequired):
        engine.unlock(protected, "")


def test_protect_requires_a_password():
    with pytest.raises(PDFEngineError, match="open password is required"):
        engine.protect(corpus.multipage_pdf(1), "")


# ---------------------------------------------------------- compression

def test_compression_reduces_a_compressible_document():
    big = engine.watermark_text(corpus.multipage_pdf(30), "DRAFT")
    result = engine.compress(big, "maximum-compression")

    assert result.compressed_bytes < result.original_bytes
    assert result.reduction_percent > 0
    # The measured figure must match the actual bytes returned.
    assert result.compressed_bytes == len(result.data)
    assert len(reopen(result.data).pages) == 30


def test_compression_never_reports_a_negative_saving():
    """If output would be larger, the original is kept and 0% reported."""
    result = engine.compress(corpus.clean_pdf(), "maximum-quality")
    assert result.reduction_percent >= 0
    assert result.compressed_bytes <= result.original_bytes


def test_compression_rejects_unknown_preset():
    with pytest.raises(PDFEngineError, match="Unknown preset"):
        engine.compress(corpus.clean_pdf(), "ludicrous")


def test_reduction_percent_is_computed_from_measured_bytes():
    from docintel.pdf.engine import CompressionResult
    result = CompressionResult(original_bytes=1000, compressed_bytes=250, data=b"")
    assert result.reduction_percent == 75.0

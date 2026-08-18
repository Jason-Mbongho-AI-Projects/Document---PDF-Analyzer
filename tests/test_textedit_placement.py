"""Where replacement text lands, and whether the document still reads.

Two things were wrong and are pinned here so they stay fixed.

The point size was inferred from the measured ink width, which is narrower
than the advance width, so replacements came out a few tenths small. The
baseline was found by pushing the bottom of the glyph box down by a full
descender, which is only right when the matched text contains a descender --
"four." has none, so its replacement was raised two points. Together they made
a visible superscript next to the text it replaced.

Separately, the replacement used to be drawn over the page as a new object.
It looked right once placed, but it landed last in the page's reading order,
so an edited sentence came out of extraction, export and the AI as a word
stranded at the end. Where it can be done safely the new text is now written
into the page's own text instead.
"""
import ctypes

import pypdfium2 as pypdfium
import pypdfium2.raw as pdfium_raw
import pytest

import pdf_corpus as corpus
from docintel.pdf import convert, textedit
from docintel.pdf.engine import PDFEngineError
from docintel.pdf.textedit import Edit, Style


def text_of(data: bytes) -> str:
    document = pypdfium.PdfDocument(data)
    return "\n".join(page.get_textpage().get_text_range() for page in document)


def glyphs(data: bytes, page: int = 0):
    """Every visible character with its true baseline and point size."""
    text_page = pypdfium.PdfDocument(data)[page].get_textpage()
    out = []
    for index in range(text_page.count_chars()):
        char = text_page.get_text_range(index, 1)
        if not char.strip():
            continue
        x, y = ctypes.c_double(), ctypes.c_double()
        pdfium_raw.FPDFText_GetCharOrigin(
            text_page.raw, index, ctypes.byref(x), ctypes.byref(y))
        out.append((char, round(x.value, 1), round(y.value, 1),
                    round(pdfium_raw.FPDFText_GetFontSize(text_page.raw, index), 1)))
    return out


def sentence() -> bytes:
    return convert.text_to_pdf("The meeting ends at four today.")


# --------------------------------------------------------------- measuring

def test_the_original_size_is_read_from_the_page_not_guessed():
    spot = textedit.find_text(sentence(), "four")[0]
    assert spot.source_size == pytest.approx(10.0, abs=0.01)


def test_the_baseline_is_read_from_the_page_not_guessed():
    spot = textedit.find_text(sentence(), "four")[0]
    baseline = [y for char, _, y, _ in glyphs(sentence()) if char == "f"][0]
    assert spot.baseline == pytest.approx(baseline, abs=0.01)


def test_a_span_without_descenders_is_not_raised():
    """The old rule added a descender's depth whether or not there was one."""
    spot = textedit.find_text(sentence(), "four")[0]
    # The glyph box bottom and the baseline coincide when nothing descends.
    assert spot.baseline == pytest.approx(spot.y, abs=0.2)


# ------------------------------------------------------------- placement

def test_the_replacement_sits_on_the_same_baseline():
    before = [g for g in glyphs(sentence()) if g[0] == "f"][0]
    out, _ = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="five")])
    after = [g for g in glyphs(out) if g[0] == "f"][0]

    assert after[2] == pytest.approx(before[2], abs=0.01)   # baseline
    assert after[1] == pytest.approx(before[1], abs=0.5)    # left edge


def test_the_replacement_is_set_at_the_same_size():
    out, _ = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="five")])
    sizes = {size for char, _, _, size in glyphs(out)}
    assert sizes == {10.0}


def test_a_span_with_a_descender_is_also_placed_correctly():
    made = convert.text_to_pdf("Please pay the agency today.")
    before = [g for g in glyphs(made) if g[0] == "a"][0][2]
    out, _ = textedit.apply_edits(
        made, [Edit(page=1, find="agency", replace="agent")])
    after = [g for g in glyphs(out) if g[0] == "a"][0][2]
    assert after == pytest.approx(before, abs=0.01)


# --------------------------------------------------------- reading order

def test_the_edited_sentence_still_reads_in_order():
    out, _ = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="five")])
    assert text_of(out).strip() == "The meeting ends at five today."


def test_a_word_in_the_middle_does_not_move_to_the_end():
    out, _ = textedit.apply_edits(
        corpus.clean_pdf(), [Edit(page=1, find="Second", replace="Third")])
    assert "Third sentence." in text_of(out)


def test_it_reports_that_the_document_font_was_kept():
    """No substitute font is named, because none was used."""
    _, report = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="five")])
    assert report[0]["in_place"] is True
    assert report[0]["font"] is None
    assert "reading order" in report[0]["note"]


# ------------------------------------------------- when it must fall back

def test_a_chosen_font_is_honoured_rather_than_the_document_font():
    """Asking for Courier must not be quietly ignored to keep the order."""
    _, report = textedit.apply_edits(
        sentence(),
        [Edit(page=1, find="four", replace="five", style=Style(font="Courier"))])
    assert report[0].get("in_place") is not True
    assert report[0]["font"] == "Courier"


def test_a_chosen_size_is_honoured():
    _, report = textedit.apply_edits(
        sentence(),
        [Edit(page=1, find="four", replace="five", style=Style(size=18))])
    assert report[0].get("in_place") is not True
    assert report[0]["size"] == 18


def test_a_chosen_colour_is_honoured():
    _, report = textedit.apply_edits(
        sentence(),
        [Edit(page=1, find="four", replace="five", style=Style(colour="#cc0000"))])
    assert report[0].get("in_place") is not True


def test_a_wider_replacement_is_drawn_so_it_can_be_shrunk():
    """Text written into the page inherits its size and cannot be made smaller."""
    _, report = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="ninety")])
    assert report[0].get("in_place") is not True
    assert report[0]["shrunk"] is True
    assert report[0]["size"] < 10.0


def test_a_replacement_far_too_wide_still_warns_rather_than_shrinking_away():
    _, report = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="seventeen thirty")])
    assert report[0].get("in_place") is not True
    assert report[0]["overflows"] is True


def test_an_embedded_subset_font_is_never_written_into():
    """A subset carries only the glyphs already used; nothing can add to it."""
    if convert._office_binary() is None:
        pytest.skip("LibreOffice is not installed on this machine")

    data = convert.to_pdf(corpus.small_docx(), "docx")
    assert textedit._keeps_its_own_font(data, 1) is False


def test_an_edit_that_cannot_be_applied_is_refused_not_faked():
    """A subset font encodes its own bytes, so the words are not in the stream.

    Nothing can be edited there without rebuilding the font, and the check
    after the edit catches it: the result is discarded and the caller is told,
    rather than being handed a document that looks edited and is not.
    """
    if convert._office_binary() is None:
        pytest.skip("LibreOffice is not installed on this machine")

    data = convert.to_pdf(corpus.small_docx(), "docx")
    with pytest.raises(PDFEngineError) as raised:
        textedit.apply_edits(data, [Edit(page=1, find="twelve", replace="nine")])
    assert "still present" in str(raised.value)


def test_deleting_text_still_removes_it_entirely():
    out, _ = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="")])
    assert "four" not in text_of(out)
    assert "The meeting ends at" in text_of(out)


def test_the_following_text_does_not_move():
    """A shorter replacement is padded so the rest of the line stays put."""
    before = [g for g in glyphs(sentence()) if g[0] == "t"][-1][1]
    out, _ = textedit.apply_edits(
        sentence(), [Edit(page=1, find="four", replace="two")])
    after = [g for g in glyphs(out) if g[0] == "t"][-1][1]
    assert after == pytest.approx(before, abs=2.0)

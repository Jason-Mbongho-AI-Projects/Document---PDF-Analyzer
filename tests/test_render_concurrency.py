"""
Concurrent rasterisation must not take the process down.

PDFium is not thread-safe, and FastAPI runs synchronous endpoints in a thread
pool, so two page renders arriving together land on different threads and race
inside the native library. The failure is not an exception that a test would
catch as a failed assertion — it is a segmentation fault that kills the whole
worker, so an unlocked build does not fail these tests, it crashes the run.

That is exactly why they are worth having: a regression here does not produce
a red test, it produces a dead server and a connection reset in the browser.
"""
import io
from concurrent.futures import ThreadPoolExecutor

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docintel.pdf.render import render_page, render_thumbnails
from docintel.pdf.text import extract, search


def make_pdf(pages: int = 4) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for number in range(1, pages + 1):
        pdf.setFont("Helvetica", 18)
        pdf.drawString(72, 700, f"Concurrency page {number}")
        pdf.drawString(72, 660, "The quick brown fox jumps over the lazy dog.")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_many_concurrent_renders_all_succeed():
    data = make_pdf()

    def one(index: int):
        return render_page(data, (index % 4) + 1, scale=1.5, fmt="png")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(48)))

    assert len(results) == 48
    assert all(r.data.startswith(b"\x89PNG") for r in results)
    assert all(r.width > 0 and r.height > 0 for r in results)


def test_rendering_and_text_extraction_can_run_together():
    """Both use PDFium, so they must share one lock rather than have two.

    Two separate locks would not exclude each other and the race would simply
    move from render-versus-render to render-versus-extract.
    """
    data = make_pdf()

    def render(_):
        return render_page(data, 1, scale=1.2).width > 0

    def read(_):
        return len(extract(data)) == 4

    def find(_):
        return len(search(data, "quick")) >= 1

    with ThreadPoolExecutor(max_workers=9) as pool:
        jobs = []
        for index in range(12):
            jobs.append(pool.submit(render, index))
            jobs.append(pool.submit(read, index))
            jobs.append(pool.submit(find, index))
        results = [job.result() for job in jobs]

    assert all(results)


def test_concurrent_thumbnailing_is_safe():
    data = make_pdf(6)

    with ThreadPoolExecutor(max_workers=6) as pool:
        batches = list(pool.map(lambda _: render_thumbnails(data), range(12)))

    assert all(len(batch) == 6 for batch in batches)


def test_repeated_sequential_renders_stay_correct():
    """The crash in the field appeared on the fourth render of a document."""
    data = make_pdf(1)
    sizes = {render_page(data, 1, scale=1.5).data for _ in range(10)}

    # Deterministic input, deterministic output: every render identical.
    assert len(sizes) == 1

"""
PDF AI Summarizer — document intelligence workspace.

UI layer only: extraction and summarisation run through PDFProcessor /
PDFSummarizer, caching through CacheManager, run history through
SessionManager. Presentation lives in ui_theme.py.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
from dotenv import load_dotenv

from pdf_processor import PDFProcessor
from summarizer import PDFSummarizer
from utils import export_summary_to_text, create_summary_dataframe
from cache_manager import cache_manager, CacheManager
from session_manager import session_manager
from security_analyzer import security_analyzer
from config import Config
import prompt_guard

import ui_theme as ui

st.set_page_config(
    page_title="PDF AI Summarizer",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.inject()

SUMMARY_MODES = {
    "brief": ("Brief", "Two or three sentences — the gist and nothing else."),
    "detailed": ("Detailed", "Themes, arguments, supporting detail and conclusions."),
    "bullet_points": ("Bullets", "Grouped, scannable points with sub-bullets."),
    "executive": ("Executive", "Insights, implications, recommendations and risks."),
}

STEPS = [
    ("Extract text from PDF", "pypdf"),
    ("Normalise and tokenise", "per page"),
    ("Segment into chunks", "token-bounded"),
    ("Read document structure", "optional"),
    ("Summarise sections", "in parallel"),
    ("Synthesise final summary", "streaming"),
    ("Pull key quotes", "optional"),
]


# --------------------------------------------------------------------- setup

def api_key_ready() -> tuple[bool, str, str]:
    """Return (ok, badge_kind, message) for the credential panel."""
    load_dotenv()
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return False, "bad", "No API key found"
    if len(key) < 30:
        return False, "warn", "Key looks truncated"
    return True, "ok", f"Connected · …{key[-4:]}"


@st.cache_resource
def get_processor() -> PDFProcessor:
    return PDFProcessor()


@st.cache_resource
def get_summarizer() -> PDFSummarizer:
    return PDFSummarizer()


def eta(num_chunks: int, workers: int) -> str:
    """Model time is roughly 4s per section, divided across workers, plus the
    synthesis pass."""
    import math
    seconds = math.ceil(num_chunks / max(workers, 1)) * 4 + 6
    if seconds < 90:
        return f"~{seconds}s"
    return f"~{seconds // 60}m {seconds % 60}s"


# ------------------------------------------------------------------ sidebar

def render_sidebar():
    ui.sidebar_brand("PDF Summarizer", "Document Intelligence")

    ok, kind, message = api_key_ready()
    st.sidebar.markdown(ui.badge(message, kind), unsafe_allow_html=True)

    ui.sidebar_label("Summary mode")
    labels = {v[0]: k for k, v in SUMMARY_MODES.items()}
    picked = st.sidebar.segmented_control(
        "Summary mode",
        options=list(labels.keys()),
        default="Detailed",
        label_visibility="collapsed",
    )
    summary_type = labels.get(picked, "detailed")
    st.sidebar.caption(SUMMARY_MODES[summary_type][1])

    ui.sidebar_label("Chunk size")
    max_tokens = st.sidebar.slider(
        "Max tokens per chunk", 4000, 10000, 8000, step=500,
        label_visibility="collapsed",
        help="Larger chunks mean fewer model calls and more context per call.",
    )

    ui.sidebar_label("Concurrency")
    workers = st.sidebar.slider(
        "Parallel requests", 1, 8, Config.NUM_WORKER_THREADS,
        label_visibility="collapsed",
        help="How many sections to summarise at once. Lower this if OpenRouter "
             "starts rate-limiting you.",
    )

    ui.sidebar_label("Extras")
    show_analysis = st.sidebar.toggle(
        "Document analysis", value=True,
        help="Identify document type, themes, audience and purpose.",
    )
    show_quotes = st.sidebar.toggle(
        "Key quotes", value=False,
        help="Pull standout passages from the opening of the document.",
    )
    use_cache = st.sidebar.toggle(
        "Use cache", value=True,
        help="Reuse a previous result for the same file and summary mode.",
    )

    render_history()

    if st.session_state.get("results"):
        ui.sidebar_label("Session")
        if st.sidebar.button("Clear results", width="stretch"):
            st.session_state.pop("results", None)
            st.rerun()

    return {
        "key_ok": ok,
        "summary_type": summary_type,
        "max_tokens": max_tokens,
        "workers": workers,
        "show_analysis": show_analysis,
        "show_quotes": show_quotes,
        "use_cache": use_cache,
    }


def render_history() -> None:
    runs = session_manager.get_history(limit=6)
    if not runs:
        return

    ui.sidebar_label("Recent runs")
    for run in runs:
        stamp = run.get("timestamp", "")[5:16].replace("T", " ")
        meta = f"{run.get('num_chunks', 0)} · {run.get('processing_time', 0):.0f}s · {stamp}"
        st.sidebar.markdown(
            ui.run_row(run.get("file_name", "—"), meta, run.get("status") == "success"),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------- processing

def run_pipeline(uploaded_file, opts):
    processor, summarizer = get_processor(), get_summarizer()
    summary_type = opts["summary_type"]

    board = st.empty()
    meter = st.progress(0.0)
    note = st.empty()
    started = time.time()

    def stage(index: int, message: str = "", fraction: float = 0.0):
        board.markdown(ui.pipeline(STEPS, index), unsafe_allow_html=True)
        meter.progress(min(max(fraction, 0.0), 1.0))
        if message:
            note.caption(message)

    def teardown():
        board.empty(); meter.empty(); note.empty()

    # 1 — extract
    stage(0, "Reading pages…", 0.02)
    uploaded_file.seek(0)
    try:
        raw_text = processor.extract_text_from_pdf(uploaded_file)
    except Exception as exc:
        teardown()
        st.error(f"Could not extract text: {exc}")
        return None
    if not raw_text.strip():
        teardown()
        st.error("No extractable text found — this PDF is likely a scan and needs OCR.")
        return None

    # 2 — clean, one page at a time so provenance survives
    stage(1, f"{len(raw_text):,} characters extracted", 0.10)
    pages = processor.clean_pages(raw_text)
    if not pages:
        teardown()
        st.error("Text could not be normalised.")
        return None
    clean_text = " ".join(p["text"] for p in pages)
    token_count = processor.count_tokens(clean_text)

    # The text is fenced as data before it reaches the model regardless; this
    # only tells the user that their document attempted an injection.
    injection = prompt_guard.scan(clean_text)

    # 3 — chunk, carrying page ranges
    stage(2, f"~{token_count:,} tokens across {len(pages)} pages", 0.16)
    chunks = processor.chunk_pages(pages, opts["max_tokens"])
    if not chunks:
        teardown()
        st.error("Text could not be segmented into chunks.")
        return None

    workers = min(opts["workers"], len(chunks))

    # 4 — structure
    analysis = None
    if opts["show_analysis"]:
        stage(3, f"{len(chunks)} sections · est. {eta(len(chunks), workers)}", 0.22)
        result = summarizer.analyze_document_structure(clean_text)
        analysis = result["analysis"] if result["status"] == "success" else None

    # 5 — summarise sections concurrently
    summaries = [None] * len(chunks)
    span = 0.55
    completed = 0
    stage(4, f"Summarising {len(chunks)} sections across {workers} workers…", 0.25)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(summarizer.summarize_text, chunk["text"], summary_type): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(pending):
            i = pending[future]
            chunk = chunks[i]
            try:
                text = future.result()
            except Exception as exc:
                text = f"Error generating summary: {exc}"
            failed = text.startswith("Error generating summary")
            summaries[i] = {
                "chunk_number": i + 1,
                "summary": text,
                "original_length": len(chunk["text"]),
                "summary_length": 0 if failed else len(text),
                "first_page": chunk["first_page"],
                "last_page": chunk["last_page"],
            }
            completed += 1
            stage(4, f"{completed} of {len(chunks)} sections summarised",
                  0.25 + span * (completed / len(chunks)))

    # 6 — synthesise, streamed into the results panel
    stage(5, "Merging section summaries…", 0.82)
    combined = stream_synthesis(summarizer, summaries, summary_type)
    stage(5, "", 0.94)

    # 7 — quotes
    quotes = []
    if opts["show_quotes"]:
        stage(6, "Selecting key passages…", 0.96)
        quotes = summarizer.extract_key_quotes(clean_text[:5000])

    stage(len(STEPS), "", 1.0)
    time.sleep(0.3)
    teardown()

    return {
        "file_name": uploaded_file.name,
        "summary_type": summary_type,
        "total_chunks": len(chunks),
        "individual_summaries": summaries,
        "combined_summary": combined,
        "analysis": analysis,
        "quotes": quotes,
        "char_count": len(clean_text),
        "token_count": token_count,
        "page_count": len(pages),
        "workers": workers,
        "elapsed": time.time() - started,
        "injection": {
            "detected": injection.detected,
            "summary": injection.summary,
            "matches": [
                {"category": m.category, "excerpt": m.excerpt}
                for m in injection.matches[:8]
            ],
        },
    }


def stream_synthesis(summarizer, summaries, summary_type: str) -> str:
    """Render the final synthesis as it arrives, then return the full text."""
    ui.section("◆", "Final summary", "Synthesising across every section")

    accumulated = []
    with st.container(key="prose_live"):
        slot = st.empty()
        for delta in summarizer.stream_combined_summary(summaries, summary_type):
            accumulated.append(delta)
            # st.markdown renders the model's markdown and escapes its HTML,
            # so document text is never injected into the page as markup.
            slot.markdown("".join(accumulated) + " ▍")
        slot.markdown("".join(accumulated))

    return "".join(accumulated)


# ------------------------------------------------------------------- results

def render_results(data, live: bool = False):
    frame = create_summary_dataframe(data)
    failures = int((frame["Status"] != "Success").sum())
    compression = frame["Compression Ratio"].mean() if not frame.empty else 0
    words = len(data["combined_summary"].split())

    ui.section("◆", "Results",
               f"{data['file_name']} · {SUMMARY_MODES[data['summary_type']][0].lower()} mode")

    injection = data.get("injection") or {}
    if injection.get("detected"):
        ui.risk_banner("medium", "INJECTION ATTEMPT", injection["summary"])
        with st.expander(f"Flagged passages ({len(injection['matches'])})"):
            for match in injection["matches"]:
                ui.finding_card(match["category"], "medium", f"…{match['excerpt']}…")

    cols = st.columns(4)
    stats = [
        ("Sections", f"{data['total_chunks']}",
         f"{failures} failed" if failures else "all succeeded"),
        ("Compression", f"{compression:.0%}", "summary vs source"),
        ("Summary length", f"{words:,}", "words"),
        ("Elapsed", f"{data['elapsed']:.0f}s",
         f"{data.get('workers', 1)} workers" if data.get("workers", 1) > 1 else "single worker"),
    ]
    for col, (key, value, sub) in zip(cols, stats):
        with col:
            ui.plate(key, value, sub)

    names = [] if live else ["Summary"]
    if data["analysis"]:
        names.append("Structure")
    if data["quotes"]:
        names.append("Quotes")
    names += ["Sections", "Export"]
    tabs = st.tabs(names)
    panel = dict(zip(names, tabs))

    if "Summary" in panel:
        with panel["Summary"]:
            with st.container(key="prose_summary"):
                st.markdown(data["combined_summary"])

    if "Structure" in panel:
        with panel["Structure"]:
            with st.container(key="prose_structure"):
                st.markdown(data["analysis"])

    if "Quotes" in panel:
        with panel["Quotes"]:
            for quote in data["quotes"]:
                st.markdown(f'<div class="quote">{quote}</div>', unsafe_allow_html=True)

    with panel["Sections"]:
        for item in data["individual_summaries"]:
            failed = item["summary_length"] == 0
            tag = ui.pages_tag(item.get("first_page"), item.get("last_page"))
            label = f"Section {item['chunk_number']} · {item['original_length']:,} chars"
            with st.expander(label + ("  —  failed" if failed else "")):
                if tag:
                    st.markdown(f"Source {tag}", unsafe_allow_html=True)
                st.write(item["summary"])

    with panel["Export"]:
        stem = data["file_name"].rsplit(".", 1)[0]
        left, right = st.columns(2)
        with left:
            st.download_button(
                "Summary report (.txt)",
                data=export_summary_to_text(data, data["file_name"]),
                file_name=f"{stem}_summary.txt",
                mime="text/plain",
                width="stretch",
            )
        with right:
            st.download_button(
                "Section statistics (.csv)",
                data=frame.to_csv(index=False),
                file_name=f"{stem}_stats.csv",
                mime="text/csv",
                width="stretch",
            )
        st.dataframe(frame, width="stretch", hide_index=True)


# ------------------------------------------------------------------ security

@st.cache_data(show_spinner=False)
def scan_document(file_bytes: bytes):
    """Static security scan, keyed on file content so re-runs are free.

    Returns a plain dict rather than the dataclass so it survives Streamlit's
    cache serialisation.
    """
    import io
    report = security_analyzer.analyze(io.BytesIO(file_bytes))
    return {
        "risk_level": report.risk_level,
        "risk_label": report.risk_label,
        "headline": report.headline,
        "checks_run": report.checks_run,
        "encrypted": report.encrypted,
        "signed": report.signed,
        "has_forms": report.has_forms,
        "url_count": len(report.urls),
        "findings": [
            {
                "title": f.title,
                "severity": f.severity,
                "detail": f.detail,
                "locations": f.location_summary,
            }
            for f in report.by_severity()
        ],
    }


def render_security(uploaded_file) -> None:
    report = scan_document(uploaded_file.getvalue())

    ui.section("⚿", "Security scan",
               "Static structural inspection — nothing in the document is executed or fetched")
    ui.risk_banner(report["risk_level"], report["risk_label"], report["headline"])

    if report["findings"]:
        for finding in report["findings"]:
            ui.finding_card(finding["title"], finding["severity"],
                            finding["detail"], finding["locations"])

    with st.expander(f"Checks performed ({len(report['checks_run'])}) · "
                     f"{report['url_count']} external URL(s) found"):
        ui.checklist(report["checks_run"])
        st.caption(
            "These are static checks on PDF structure. They cannot detect every "
            "threat and cannot establish that a document is safe. Treat the result "
            "as one signal, not a verdict."
        )


# ---------------------------------------------------------------------- main

def main():
    ui.hero(
        title="Read less. <em>Understand more.</em>",
        subtitle="Drop in a PDF and get a structured, source-faithful summary — "
                 "segmented by page, summarised in parallel, then synthesised into one coherent read.",
        eyebrow="Document Intelligence",
        chips=["◆ Page-anchored sections", "◆ Parallel summarisation",
               "◆ Streaming synthesis", "◆ Cached re-runs"],
    )

    opts = render_sidebar()

    if not opts["key_ok"]:
        ui.section("!", "Credentials required",
                   "The summariser needs an OpenRouter key before it can run")
        ui.empty("🔑", "Add OPENROUTER_API_KEY to your .env",
                 "Create a .env file next to app.py containing OPENROUTER_API_KEY=… — "
                 "keys are issued at openrouter.ai/keys.")
        return

    ui.section("↑", "Source document",
               "PDF with a selectable text layer — scans need OCR first")
    uploaded_file = st.file_uploader(
        "Source document", type="pdf", label_visibility="collapsed",
        help="The file is read in memory and never written to disk.",
    )

    if uploaded_file is None:
        ui.empty("◫", "No document loaded",
                 "Drop a PDF above, choose a summary mode in the sidebar, and run the pipeline.")
        if st.session_state.get("results"):
            render_results(st.session_state["results"])
        return

    uploaded_file.seek(0)
    metadata = get_processor().get_pdf_metadata(uploaded_file)
    if "error" in metadata:
        st.error(f"Could not read this PDF: {metadata['error']}")
        return

    def field(value, fallback="—"):
        text = str(value).strip() if value else ""
        return text if text and text != "Unknown" else fallback

    ui.section("◫", "Document profile", "Metadata read from the PDF header")
    cols = st.columns(4)
    facets = [
        ("📄", "Pages", field(metadata.get("num_pages")), f"{uploaded_file.size / 1024:.0f} KB"),
        ("🏷", "Title", field(metadata.get("title"), uploaded_file.name), "from metadata"),
        ("👤", "Author", field(metadata.get("author")), "from metadata"),
        ("🗂", "Subject", field(metadata.get("subject")), "from metadata"),
    ]
    for col, (icon, key, value, sub) in zip(cols, facets):
        with col:
            ui.facet(icon, key, value, sub)

    render_security(uploaded_file)

    file_hash = CacheManager._compute_file_hash(uploaded_file.getvalue())
    cached = (cache_manager.get(uploaded_file.name, file_hash, opts["summary_type"])
              if opts["use_cache"] else None)

    st.write("")
    left, right = st.columns([1, 2.4], vertical_alignment="center")
    with left:
        launch = st.button("Run analysis", type="primary", width="stretch")
    with right:
        extras = [SUMMARY_MODES[opts["summary_type"]][0], f"{opts['max_tokens']:,} tokens/chunk",
                  f"{opts['workers']}× parallel"]
        if opts["show_analysis"]:
            extras.append("structure")
        if opts["show_quotes"]:
            extras.append("quotes")
        chips = " ".join(ui.badge(x, "info") for x in extras)
        if cached:
            chips += " " + ui.badge("cached result available", "ok")
        st.markdown(chips, unsafe_allow_html=True)

    if launch:
        if cached:
            st.session_state["results"] = cached
            st.toast("Loaded from cache — no model calls made.")
        else:
            results = run_pipeline(uploaded_file, opts)
            if results is None:
                return
            st.session_state["results"] = results
            cache_manager.set(uploaded_file.name, file_hash, opts["summary_type"], results)
            session_manager.add_session(
                file_name=uploaded_file.name,
                file_size=uploaded_file.size,
                summary_type=opts["summary_type"],
                processing_time=results["elapsed"],
                num_chunks=results["total_chunks"],
                status="success",
            )
            # The synthesis already streamed above; skip re-rendering it.
            render_results(results, live=True)
            return

    if st.session_state.get("results"):
        render_results(st.session_state["results"])


if __name__ == "__main__":
    main()

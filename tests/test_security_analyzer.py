"""
Security analyzer tests.

Each case builds a real PDF containing a real construct, runs it through the
analyzer exactly as an upload would be, and asserts on the finding produced.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security_analyzer import PDFSecurityAnalyzer, _classify_url  # noqa: E402
import pdf_corpus as corpus  # noqa: E402


@pytest.fixture
def analyzer():
    return PDFSecurityAnalyzer()


def ids(report):
    return {f.id for f in report.findings}


# ------------------------------------------------------------- clean files

def test_clean_pdf_reports_no_indicators(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.clean_pdf()))
    assert report.findings == []
    assert report.risk_level == "none"
    assert report.risk_label == "NO INDICATORS"


def test_clean_report_never_claims_safety(analyzer):
    """RULE: a clean scan must not assert the file is safe.

    The sanctioned disclaimer legitimately contains the word "safe" inside a
    negation, so strip that clause before hunting for affirmative claims.
    """
    report = analyzer.analyze(corpus.as_stream(corpus.clean_pdf()))
    headline = report.headline.lower()

    assert "no suspicious indicators were detected" in headline
    disclaimer = "this is not a guarantee that the file is safe."
    assert disclaimer in headline

    remainder = headline.replace(disclaimer, "")
    for claim in ("is safe", "safe to open", "clean and safe", "no risk", "verified safe"):
        assert claim not in remainder


def test_checks_run_is_reported(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.clean_pdf()))
    assert len(report.checks_run) >= 8


# ----------------------------------------------------------- active content

def test_detects_javascript_in_name_tree(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.javascript_pdf()))
    assert "javascript" in ids(report)
    finding = next(f for f in report.findings if f.id == "javascript")
    assert finding.severity == "high"


def test_detects_open_action(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.open_action_javascript_pdf()))
    assert "open_action" in ids(report)
    finding = next(f for f in report.findings if f.id == "open_action")
    assert finding.severity == "high"


def test_detects_launch_action(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.launch_action_pdf()))
    assert "launch_action" in ids(report)
    assert report.risk_level == "high"


def test_detects_document_additional_actions(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.additional_actions_pdf()))
    assert "doc_additional_actions" in ids(report)


def test_detects_page_additional_actions(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.page_additional_actions_pdf()))
    assert "page_additional_actions" in ids(report)


def test_detects_remote_goto(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.remote_goto_pdf()))
    assert "remote_goto" in ids(report)


# --------------------------------------------------------- embedded content

def test_detects_embedded_file_and_names_it(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.embedded_file_pdf("payload.exe")))
    assert "embedded_files" in ids(report)
    finding = next(f for f in report.findings if f.id == "embedded_files")
    assert finding.severity == "high"          # executable attachment
    assert any("payload.exe" in loc for loc in finding.locations)


def test_benign_attachment_is_lower_severity(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.embedded_file_pdf("notes.txt")))
    finding = next(f for f in report.findings if f.id == "embedded_files")
    assert finding.severity == "medium"


# ------------------------------------------------------------ forms + sigs

def test_detects_signature_field_without_claiming_validity(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.signature_field_pdf()))
    assert report.signed is True
    finding = next(f for f in report.findings if f.id == "signature_field")
    assert "does not perform" in finding.detail
    assert finding.severity == "info"


def test_detects_xfa_form(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.xfa_form_pdf()))
    assert "xfa_form" in ids(report)
    assert report.has_forms is True


# -------------------------------------------------------------------- URLs

def test_flags_suspicious_url(analyzer):
    report = analyzer.analyze(
        corpus.as_stream(corpus.suspicious_url_pdf("http://192.168.1.10/payload.exe"))
    )
    assert "suspicious_urls" in ids(report)
    assert report.suspicious_urls


def test_benign_url_is_collected_but_not_flagged(analyzer):
    report = analyzer.analyze(
        corpus.as_stream(corpus.benign_url_pdf("https://example.com/report"))
    )
    assert any("example.com" in u for u in report.urls)
    assert "suspicious_urls" not in ids(report)


@pytest.mark.parametrize("url,expected", [
    ("http://192.168.0.1/a",              "bare IP"),
    ("https://bit.ly/xyz",                "shortener"),
    ("https://xn--80ak6aa92e.com/",       "punycode"),
    ("https://example.com/setup.exe",     "executable"),
    ("https://user@evil.com/",            "authority"),
    ("https://example.com:4444/x",        "non-standard port"),
])
def test_url_heuristics_fire(url, expected):
    reason = _classify_url(url)
    assert reason is not None, f"{url} should have been flagged"


@pytest.mark.parametrize("url", [
    "https://example.com/report.pdf",
    "http://example.org/docs/page",
    "https://sub.domain.co.uk:443/a/b",
    "https://example.com:8080/api",
])
def test_url_heuristics_do_not_fire_on_ordinary_links(url):
    assert _classify_url(url) is None


# ------------------------------------------------------------- resilience

def test_corrupt_pdf_is_handled_not_raised(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.corrupt_pdf()))
    assert report.parse_error is not None
    assert "unparseable" in ids(report)
    assert "could not be fully parsed" in report.headline


def test_analyzer_never_executes_or_fetches(analyzer, monkeypatch):
    """Nothing in the analyzer may open a socket."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("analyzer attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    report = analyzer.analyze(
        corpus.as_stream(corpus.suspicious_url_pdf("http://192.168.1.10/payload.exe"))
    )
    assert "suspicious_urls" in ids(report)


def test_multipage_document_scans_every_page(analyzer):
    report = analyzer.analyze(corpus.as_stream(corpus.multipage_pdf(5)))
    assert report.parse_error is None
    assert report.findings == []


def test_risk_level_is_the_maximum_severity(analyzer):
    """A file with both info and high findings must roll up to high."""
    report = analyzer.analyze(corpus.as_stream(corpus.launch_action_pdf()))
    assert report.risk_level == "high"
    ordered = report.by_severity()
    assert ordered[0].severity == "high"

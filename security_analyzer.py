"""
Static PDF security analyzer.

Walks the PDF object graph looking for the constructs that make a PDF an
attack vector: automatic actions, embedded JavaScript, embedded files, launch
actions, remote references and suspicious URLs. Nothing here executes,
resolves, or fetches anything from the document — it only inspects structure.

Scope, stated honestly: these are static structural checks. They cannot prove
a document is safe. A clean report means "no suspicious indicators were
detected by the checks below", never "this file is safe". Report wording is
held to that standard deliberately.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

import pypdf

from logger_config import setup_logger

logger = setup_logger(__name__)

# Severity ordering, low to high. Used to roll findings up into a risk level.
SEVERITIES = ["info", "low", "medium", "high"]

RISK_LABELS = {
    "none": "NO INDICATORS",
    "info": "INFORMATIONAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}

# Action types that run or reach outside the document when a viewer opens it.
ACTIVE_ACTIONS = {
    "/JavaScript": ("javascript_action", "JavaScript action", "high"),
    "/Launch": ("launch_action", "Launch action", "high"),
    "/SubmitForm": ("submit_form", "Form submission action", "medium"),
    "/ImportData": ("import_data", "Import-data action", "medium"),
    "/GoToR": ("remote_goto", "Remote go-to action", "medium"),
    "/GoToE": ("embedded_goto", "Embedded go-to action", "medium"),
    "/Movie": ("movie_action", "Movie action", "low"),
    "/Sound": ("sound_action", "Sound action", "low"),
    "/RichMediaExecute": ("richmedia_execute", "RichMedia execute action", "high"),
}

URL_RE = re.compile(rb"(?:https?|ftp)://[^\s<>\"')\]}]{4,400}")

SHORTENERS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd", "buff.ly",
    "adf.ly", "cutt.ly", "rebrand.ly", "shorturl.at", "rb.gy", "tiny.cc",
}

RISKY_DOWNLOAD_EXT = (
    ".exe", ".scr", ".msi", ".bat", ".cmd", ".com", ".pif", ".vbs", ".js",
    ".jar", ".ps1", ".hta", ".dll", ".apk", ".iso", ".lnk",
)

IPV4_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


@dataclass
class Finding:
    """One structural indicator found in the document."""
    id: str
    title: str
    severity: str
    detail: str
    locations: List[str] = field(default_factory=list)

    @property
    def location_summary(self) -> str:
        if not self.locations:
            return ""
        shown = self.locations[:6]
        extra = len(self.locations) - len(shown)
        text = ", ".join(shown)
        return f"{text} (+{extra} more)" if extra else text


@dataclass
class SecurityReport:
    findings: List[Finding] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    suspicious_urls: List[Dict[str, str]] = field(default_factory=list)
    encrypted: bool = False
    signed: bool = False
    has_forms: bool = False
    parse_error: Optional[str] = None

    @property
    def risk_level(self) -> str:
        if not self.findings:
            return "none"
        return max((f.severity for f in self.findings), key=SEVERITIES.index)

    @property
    def risk_label(self) -> str:
        return RISK_LABELS[self.risk_level]

    @property
    def headline(self) -> str:
        """Deliberately worded to never assert safety."""
        if self.parse_error:
            return "The document could not be fully parsed; results are incomplete."
        if not self.findings:
            return ("No suspicious indicators were detected by the available "
                    "static checks. This is not a guarantee that the file is safe.")
        counts = {}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        parts = [f"{counts[s]} {s}" for s in reversed(SEVERITIES) if s in counts]
        return f"{len(self.findings)} indicator(s) detected — {', '.join(parts)}."

    def by_severity(self) -> List[Finding]:
        return sorted(self.findings, key=lambda f: SEVERITIES.index(f.severity), reverse=True)


def _resolve(obj: Any) -> Any:
    """Resolve an indirect reference without raising on a broken xref."""
    try:
        return obj.get_object() if hasattr(obj, "get_object") else obj
    except Exception:
        return None


def _walk(node: Any, seen: Set[int], depth: int = 0, limit: int = 40) -> Iterable[Any]:
    """Yield every dictionary reachable from node.

    PDFs may contain reference cycles and hostile nesting depth, so this is
    bounded on both: visited objects are tracked by identity and recursion is
    capped. A malicious file must not be able to hang the analyzer.
    """
    if depth > limit:
        return
    node = _resolve(node)
    if node is None:
        return

    marker = id(node)
    if marker in seen:
        return
    seen.add(marker)

    if isinstance(node, dict):
        yield node
        for value in list(node.values()):
            yield from _walk(value, seen, depth + 1, limit)
    elif isinstance(node, list):
        for value in list(node):
            yield from _walk(value, seen, depth + 1, limit)


def _classify_url(url: str) -> Optional[str]:
    """Return a reason string when a URL looks worth flagging, else None."""
    lowered = url.lower()

    authority = lowered.split("://", 1)[-1].split("/", 1)[0]
    if "@" in authority:
        return "credentials or misleading authority in the host portion"

    host = authority.split("@")[-1].split(":")[0]

    if IPV4_HOST_RE.match(host):
        return "links to a bare IP address rather than a hostname"
    if host.startswith("xn--") or ".xn--" in host:
        return "internationalised (punycode) domain, a common homograph trick"
    if host in SHORTENERS:
        return "URL shortener conceals the real destination"
    if lowered.split("?")[0].endswith(RISKY_DOWNLOAD_EXT):
        return "points directly at an executable or script file"

    port = authority.split(":")[-1] if ":" in authority.split("@")[-1] else ""
    if port.isdigit() and port not in {"80", "443", "8080"}:
        return f"non-standard port {port}"

    return None


class PDFSecurityAnalyzer:
    """Static structural inspection of a PDF."""

    def analyze(self, pdf_file) -> SecurityReport:
        report = SecurityReport()
        report.checks_run = [
            "Encryption and permissions",
            "Document-level automatic actions (/OpenAction, /AA)",
            "Embedded JavaScript",
            "Embedded and attached files",
            "Launch, remote go-to and submit-form actions",
            "Page and annotation actions",
            "Interactive forms and XFA",
            "Digital signature fields",
            "External URLs and suspicious-link heuristics",
            "Raw-stream keyword sweep",
        ]

        try:
            pdf_file.seek(0)
            raw = pdf_file.read()
            pdf_file.seek(0)
            reader = pypdf.PdfReader(pdf_file)
        except Exception as exc:
            report.parse_error = str(exc)
            report.findings.append(Finding(
                id="unparseable",
                title="Document could not be parsed",
                severity="medium",
                detail=(f"The PDF structure could not be read ({exc}). Malformed "
                        "structure is itself sometimes used to evade scanners."),
            ))
            return report

        self._check_encryption(reader, report)

        try:
            catalog = _resolve(reader.trailer.get("/Root")) or {}
        except Exception:
            catalog = {}

        self._check_document_actions(catalog, report)
        self._check_javascript(catalog, reader, report)
        self._check_embedded_files(catalog, report)
        self._check_forms_and_signatures(catalog, report)
        self._check_pages(reader, report)
        self._check_urls(raw, report)
        self._check_raw_keywords(raw, report)

        return report

    # ------------------------------------------------------------- checks

    def _check_encryption(self, reader, report: SecurityReport) -> None:
        try:
            report.encrypted = bool(reader.is_encrypted)
        except Exception:
            report.encrypted = False

        if report.encrypted:
            report.findings.append(Finding(
                id="encrypted",
                title="Document is encrypted",
                severity="info",
                detail=("The file is encrypted. Encryption is normal for protected "
                        "documents, but it also limits how much of the structure "
                        "these checks can inspect."),
            ))

    def _check_document_actions(self, catalog: Dict, report: SecurityReport) -> None:
        if "/OpenAction" in catalog:
            action = _resolve(catalog["/OpenAction"])
            kind = self._action_kind(action)
            report.findings.append(Finding(
                id="open_action",
                title="Automatic action on open",
                severity="high" if kind in ("/JavaScript", "/Launch") else "medium",
                detail=(f"The document defines an /OpenAction ({kind or 'unspecified type'}) "
                        "that a viewer runs as soon as the file is opened, with no user "
                        "interaction."),
                locations=["document catalog"],
            ))

        if "/AA" in catalog:
            report.findings.append(Finding(
                id="doc_additional_actions",
                title="Document-level additional actions",
                severity="medium",
                detail=("The catalog defines /AA additional actions, which fire on "
                        "events such as document close, print or save."),
                locations=["document catalog"],
            ))

    @staticmethod
    def _action_kind(action: Any) -> Optional[str]:
        action = _resolve(action)
        if isinstance(action, dict):
            subtype = action.get("/S")
            return str(subtype) if subtype else None
        return None

    def _check_javascript(self, catalog: Dict, reader, report: SecurityReport) -> None:
        locations: List[str] = []

        names = _resolve(catalog.get("/Names")) or {}
        if isinstance(names, dict) and "/JavaScript" in names:
            locations.append("document name tree (/Names /JavaScript)")

        # JavaScript can also hang off any action anywhere in the graph.
        seen: Set[int] = set()
        for node in _walk(catalog, seen):
            if not isinstance(node, dict):
                continue
            if node.get("/S") == "/JavaScript" or "/JS" in node:
                locations.append("action object")
                break

        if locations:
            report.findings.append(Finding(
                id="javascript",
                title="Embedded JavaScript",
                severity="high",
                detail=("The document contains JavaScript. PDF JavaScript is a common "
                        "exploit and phishing vector. This analyzer never executes it."),
                locations=sorted(set(locations)),
            ))

    def _check_embedded_files(self, catalog: Dict, report: SecurityReport) -> None:
        names = _resolve(catalog.get("/Names")) or {}
        attachments: List[str] = []

        if isinstance(names, dict) and "/EmbeddedFiles" in names:
            tree = _resolve(names["/EmbeddedFiles"]) or {}
            attachments.extend(self._embedded_names(tree))

        if attachments:
            report.findings.append(Finding(
                id="embedded_files",
                title="Embedded files",
                severity="high" if any(
                    n.lower().endswith(RISKY_DOWNLOAD_EXT) for n in attachments
                ) else "medium",
                detail=("The document carries embedded file attachments. Attachments "
                        "can deliver executable payloads. They are never opened or "
                        "extracted by this analyzer."),
                locations=attachments,
            ))

    def _embedded_names(self, tree: Any, depth: int = 0) -> List[str]:
        """Read filenames out of an /EmbeddedFiles name tree."""
        if depth > 12:
            return []
        tree = _resolve(tree)
        if not isinstance(tree, dict):
            return []

        found: List[str] = []

        pairs = _resolve(tree.get("/Names"))
        if isinstance(pairs, list):
            # Name trees alternate [name, value, name, value, ...]
            for i in range(0, len(pairs) - 1, 2):
                label = _resolve(pairs[i])
                found.append(str(label))

        kids = _resolve(tree.get("/Kids"))
        if isinstance(kids, list):
            for kid in kids:
                found.extend(self._embedded_names(kid, depth + 1))

        return found

    def _check_forms_and_signatures(self, catalog: Dict, report: SecurityReport) -> None:
        acroform = _resolve(catalog.get("/AcroForm"))
        if not isinstance(acroform, dict):
            return

        report.has_forms = True

        if "/XFA" in acroform:
            report.findings.append(Finding(
                id="xfa_form",
                title="XFA form present",
                severity="medium",
                detail=("The document uses an XFA (XML Forms Architecture) form. XFA "
                        "is deprecated, unevenly supported, and has a history of "
                        "parser vulnerabilities."),
                locations=["/AcroForm /XFA"],
            ))

        fields = _resolve(acroform.get("/Fields"))
        signature_fields: List[str] = []
        if isinstance(fields, list):
            for ref in fields:
                node = _resolve(ref)
                if isinstance(node, dict) and node.get("/FT") == "/Sig":
                    name = node.get("/T")
                    signature_fields.append(str(name) if name else "unnamed field")

        if signature_fields:
            report.signed = True
            report.findings.append(Finding(
                id="signature_field",
                title="Digital signature field present",
                severity="info",
                detail=("The document contains one or more signature fields. This "
                        "analyzer records their presence only — it does not perform "
                        "cryptographic validation, so signature validity is unknown."),
                locations=signature_fields,
            ))

    def _check_pages(self, reader, report: SecurityReport) -> None:
        action_hits: Dict[str, List[str]] = {}

        try:
            pages = list(reader.pages)
        except Exception as exc:
            report.parse_error = report.parse_error or str(exc)
            return

        for index, page in enumerate(pages, start=1):
            try:
                page_obj = _resolve(page)
            except Exception:
                continue
            if not isinstance(page_obj, dict):
                continue

            if "/AA" in page_obj:
                action_hits.setdefault("page_additional_actions", []).append(f"page {index}")

            annots = _resolve(page_obj.get("/Annots"))
            if not isinstance(annots, list):
                continue

            for ref in annots:
                annot = _resolve(ref)
                if not isinstance(annot, dict):
                    continue
                action = _resolve(annot.get("/A"))
                if not isinstance(action, dict):
                    continue
                subtype = str(action.get("/S")) if action.get("/S") else None
                if subtype in ACTIVE_ACTIONS:
                    key, _, _ = ACTIVE_ACTIONS[subtype]
                    action_hits.setdefault(key, []).append(f"page {index}")

        for subtype, (key, title, severity) in ACTIVE_ACTIONS.items():
            if key in action_hits:
                report.findings.append(Finding(
                    id=key,
                    title=f"{title} in annotations",
                    severity=severity,
                    detail=(f"One or more annotations trigger a {subtype} action. "
                            "Actions of this type run or reach outside the document "
                            "when a user interacts with the annotation."),
                    locations=sorted(set(action_hits[key])),
                ))

        if "page_additional_actions" in action_hits:
            report.findings.append(Finding(
                id="page_additional_actions",
                title="Page-level additional actions",
                severity="medium",
                detail="Pages define /AA actions that fire on open or close events.",
                locations=sorted(set(action_hits["page_additional_actions"])),
            ))

    def _check_urls(self, raw: bytes, report: SecurityReport) -> None:
        found: List[str] = []
        for match in URL_RE.findall(raw):
            try:
                url = match.decode("utf-8", errors="ignore").rstrip(").,;'\"")
            except Exception:
                continue
            if url not in found:
                found.append(url)

        report.urls = found[:200]

        for url in report.urls:
            reason = _classify_url(url)
            if reason:
                report.suspicious_urls.append({"url": url, "reason": reason})

        if report.suspicious_urls:
            report.findings.append(Finding(
                id="suspicious_urls",
                title="Suspicious external links",
                severity="medium",
                detail=("Links were found whose shape matches common phishing or "
                        "malware-delivery patterns. They are never followed by this "
                        "analyzer. Judge them in context — legitimate documents do "
                        "sometimes use shorteners or non-standard ports."),
                locations=[u["url"] for u in report.suspicious_urls],
            ))

    def _check_raw_keywords(self, raw: bytes, report: SecurityReport) -> None:
        """Backstop sweep over the raw bytes.

        Object-graph walking misses constructs hidden behind a damaged xref
        table, which is exactly what an evasive file has. Anything caught here
        but not above is reported at lower confidence.
        """
        already = {f.id for f in report.findings}
        sweep = {
            b"/JavaScript": ("javascript", "JavaScript"),
            b"/Launch": ("launch_action", "a Launch action"),
            b"/EmbeddedFile": ("embedded_files", "an embedded file"),
            b"/OpenAction": ("open_action", "an automatic open action"),
            b"/RichMedia": ("richmedia", "RichMedia (Flash/3D) content"),
        }

        missed = []
        for token, (finding_id, label) in sweep.items():
            if token in raw and finding_id not in already:
                missed.append(label)

        if missed:
            report.findings.append(Finding(
                id="raw_keyword_sweep",
                title="Indicators found only in raw bytes",
                severity="low",
                detail=("A sweep of the raw file found " + ", ".join(sorted(missed)) +
                        ", which did not appear when walking the object graph. This "
                        "can mean unreferenced leftovers, or structure deliberately "
                        "hidden from parsers. Worth a manual look."),
            ))


security_analyzer = PDFSecurityAnalyzer()

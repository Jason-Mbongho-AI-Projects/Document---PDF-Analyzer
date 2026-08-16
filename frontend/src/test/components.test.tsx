/**
 * Component tests.
 *
 * These focus on the places where the UI has to tell the truth: fidelity
 * labels, unavailable engines, the open-access banner, and the redaction
 * confirmation step.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../api";
import { ConvertPanel } from "../components/ConvertPanel";
import { OcrPanel } from "../components/ToolsPanel";
import { RedactPanel, SecurityPanel } from "../components/Panels";

const notify = vi.fn();

beforeEach(() => notify.mockClear());

describe("ConvertPanel", () => {
  const targets = [
    {
      target: "txt", label: "Plain text", extension: "txt",
      fidelity: "text-only",
      fidelity_note: "Text is preserved. Layout, fonts and images are not.",
      available: true, reason: null,
    },
    {
      target: "pptx", label: "PowerPoint", extension: "pptx",
      fidelity: "structural", fidelity_note: "…",
      available: false,
      reason: "Converting to PowerPoint requires LibreOffice, which is not installed.",
    },
  ];

  it("shows the fidelity of every target", async () => {
    vi.spyOn(api, "conversionCapabilities").mockResolvedValue({
      from_pdf: targets, to_pdf: ["txt"], note: "n",
    } as never);

    render(<ConvertPanel documentId="d1" notify={notify} />);

    expect(await screen.findByText("Plain text")).toBeInTheDocument();
    expect(screen.getByText("text-only")).toBeInTheDocument();
    expect(
      screen.getByText(/Layout, fonts and images are not/),
    ).toBeInTheDocument();
  });

  it("keeps unavailable targets visible with the reason, not hidden", async () => {
    vi.spyOn(api, "conversionCapabilities").mockResolvedValue({
      from_pdf: targets, to_pdf: [], note: "n",
    } as never);

    render(<ConvertPanel documentId="d1" notify={notify} />);

    expect(await screen.findByText("PowerPoint")).toBeInTheDocument();
    expect(screen.getByText(/requires LibreOffice/)).toBeInTheDocument();

    const button = screen.getByRole("button", { name: /Unavailable/ });
    expect(button).toBeDisabled();
  });
});

describe("OcrPanel", () => {
  it("reports the assessment and explains a missing engine", async () => {
    vi.spyOn(api, "ocrAssess").mockResolvedValue({
      classification: "no_text_layer",
      summary: "No page has an extractable text layer.",
      pages_needing_ocr: [1, 2],
      pages: [],
      engine: {
        name: "none", available: false,
        reason: "No OCR engine is installed on this server. Install Tesseract…",
        languages: [],
      },
    } as never);

    render(<OcrPanel documentId="d1" notify={notify} />);

    expect(await screen.findByText(/No page has an extractable text layer/))
      .toBeInTheDocument();
    expect(screen.getByText(/OCR engine not configured/)).toBeInTheDocument();
    expect(screen.getByText(/Install Tesseract/)).toBeInTheDocument();
    // The useful part still works without an engine.
    expect(screen.getByText(/Pages without text: 1, 2/)).toBeInTheDocument();
  });

  it("offers to run OCR when an engine is present", async () => {
    vi.spyOn(api, "ocrAssess").mockResolvedValue({
      classification: "mixed", summary: "Some pages lack text.",
      pages_needing_ocr: [2], pages: [],
      engine: { name: "tesseract", available: true, reason: null, languages: ["eng"] },
    } as never);

    render(<OcrPanel documentId="d1" notify={notify} />);
    expect(await screen.findByRole("button", { name: /Run OCR \(tesseract\)/ }))
      .toBeEnabled();
  });
});

describe("SecurityPanel", () => {
  it("never presents a clean scan as proof of safety", async () => {
    vi.spyOn(api, "security").mockResolvedValue({
      document_id: "d1", scanned: true, risk_level: "none",
      risk_label: "NO INDICATORS",
      headline:
        "No suspicious indicators were detected by the available static checks. " +
        "This is not a guarantee that the file is safe.",
      findings: [],
    } as never);

    render(<SecurityPanel documentId="d1" />);

    expect(await screen.findByText(/not a guarantee/)).toBeInTheDocument();
    expect(screen.getByText("NO INDICATORS")).toBeInTheDocument();
  });

  it("orders findings with the most severe first", async () => {
    vi.spyOn(api, "security").mockResolvedValue({
      document_id: "d1", scanned: true, risk_level: "high", risk_label: "HIGH",
      headline: "2 indicators detected.",
      findings: [
        { finding_id: "javascript", title: "Embedded JavaScript",
          severity: "high", detail: "…", locations: "" },
        { finding_id: "encrypted", title: "Document is encrypted",
          severity: "info", detail: "…", locations: "" },
      ],
    } as never);

    render(<SecurityPanel documentId="d1" />);

    const headings = await screen.findAllByRole("heading", { level: 5 });
    expect(headings[0]).toHaveTextContent("Embedded JavaScript");
  });
});

describe("RedactPanel", () => {
  it("requires an explicit confirmation before destroying content", async () => {
    const user = userEvent.setup();
    const apply = vi.spyOn(api, "applyRedaction").mockResolvedValue({
      version: 2, verified: true, note: "done",
    } as never);

    vi.spyOn(api, "detectSensitive").mockResolvedValue({
      total: 1, available_kinds: ["email"],
      candidates: [{
        kind: "email", text: "alice@example.com", page: 1,
        start: 0, end: 17, rects: [],
      }],
    } as never);

    render(
      <RedactPanel documentId="d1" onPreview={() => {}} onApplied={() => {}} />,
    );

    await user.click(screen.getByRole("button", { name: /Find sensitive/ }));
    expect(await screen.findByText("alice@example.com")).toBeInTheDocument();

    // First click only asks for confirmation.
    await user.click(screen.getByRole("button", { name: /Redact 1 selected/ }));
    expect(apply).not.toHaveBeenCalled();
    expect(screen.getByText(/Permanently remove 1 item/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Yes, redact/ }));
    await waitFor(() => expect(apply).toHaveBeenCalledOnce());
  });

  it("states that detection changes nothing", async () => {
    vi.spyOn(api, "detectSensitive").mockResolvedValue({
      total: 0, available_kinds: [], candidates: [],
    } as never);

    render(
      <RedactPanel documentId="d1" onPreview={() => {}} onApplied={() => {}} />,
    );
    expect(screen.getByText(/Detection changes nothing/)).toBeInTheDocument();
  });
});

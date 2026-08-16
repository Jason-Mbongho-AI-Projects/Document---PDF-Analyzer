/**
 * Translation panel.
 *
 * The language list omitted English, on the assumption that every document
 * was already in it. That assumption breaks the moment someone opens a German
 * or Spanish source, which is exactly when translation is most wanted.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../api";
import { TranslatePanel } from "../components/ToolsPanel";

function panel(onSaved = vi.fn()) {
  return render(
    <TranslatePanel documentId="d1" pageCount={3} onSaved={onSaved} />,
  );
}

describe("target language", () => {
  it("offers English", () => {
    panel();
    const select = screen.getByLabelText(/Target language/i) as HTMLSelectElement;
    const options = [...select.options].map((o) => o.value);

    expect(options).toContain("English");
  });

  it("defaults to English", () => {
    panel();
    const select = screen.getByLabelText(/Target language/i) as HTMLSelectElement;

    expect(select.value).toBe("English");
  });

  it("still offers the other common languages", () => {
    panel();
    const select = screen.getByLabelText(/Target language/i) as HTMLSelectElement;
    const options = [...select.options].map((o) => o.value);

    for (const language of ["French", "German", "Spanish", "Simplified Chinese"]) {
      expect(options).toContain(language);
    }
  });

  it("translates into the chosen language", async () => {
    const user = userEvent.setup();
    const translate = vi.spyOn(api, "translate")
      .mockResolvedValue({
        version: 2, note: "Translated.", glossary: {}, pages: [],
      } as never);
    panel();

    await user.selectOptions(screen.getByLabelText(/Target language/i), "German");
    await user.click(screen.getByRole("button", { name: /Translate/i }));

    expect(translate).toHaveBeenCalledWith("d1", "German", undefined);
  });

  it("accepts a language that is not on the list", async () => {
    const user = userEvent.setup();
    const translate = vi.spyOn(api, "translate")
      .mockResolvedValue({
        version: 2, note: "Translated.", glossary: {}, pages: [],
      } as never);
    panel();

    await user.selectOptions(screen.getByLabelText(/Target language/i), "Other…");
    await user.type(screen.getByLabelText(/Which language/i), "Icelandic");
    await user.click(screen.getByRole("button", { name: /Translate/i }));

    expect(translate).toHaveBeenCalledWith("d1", "Icelandic", undefined);
  });

  it("will not translate into an empty custom language", async () => {
    const user = userEvent.setup();
    const translate = vi.spyOn(api, "translate")
      .mockResolvedValue({
        version: 2, note: "Translated.", glossary: {}, pages: [],
      } as never);
    panel();

    await user.selectOptions(screen.getByLabelText(/Target language/i), "Other…");
    await user.click(screen.getByRole("button", { name: /Translate/i }));

    expect(translate).not.toHaveBeenCalled();
    expect(screen.getByText(/Type the language/i)).toBeInTheDocument();
  });
});

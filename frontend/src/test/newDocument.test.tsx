/**
 * Composing a document in the app.
 *
 * The thing worth proving is that what the form shows is what gets sent: the
 * mode chooses between prose and blank pages, and the filename shown to the
 * user is the filename the document ends up with. A dialog that quietly
 * creates something other than what it previewed is worse than no dialog.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../api";
import { NewDocumentDialog } from "../components/NewDocumentDialog";
import { Library } from "../views/Library";

const notify = vi.fn();
const workspace = { id: "ws1", name: "Development", description: null };

function created() {
  return vi.spyOn(api, "createDocument").mockResolvedValue({
    document: {
      id: "new1", filename: "Notes.pdf", workspace_id: "ws1",
      size_bytes: 900, status: "processing", page_count: null,
      is_archived: false, created_at: "2026-08-18T09:00:00Z",
    },
    jobs: ["j1", "j2"],
  } as never);
}

function open(props: Partial<React.ComponentProps<typeof NewDocumentDialog>> = {}) {
  return render(
    <NewDocumentDialog
      workspaceId="ws1"
      notify={notify}
      onClose={props.onClose ?? (() => {})}
      onCreated={props.onCreated ?? (() => {})}
    />,
  );
}

beforeEach(() => {
  notify.mockClear();
  vi.restoreAllMocks();
});

describe("writing a document", () => {
  it("sends the title and content that were typed", async () => {
    const user = userEvent.setup();
    const create = created();
    open();

    await user.type(screen.getByPlaceholderText("Untitled"), "Board meeting");
    await user.type(screen.getByRole("textbox", { name: /Content/ }), "Revenue is up.");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({
      workspace_id: "ws1",
      title: "Board meeting",
      content: "Revenue is up.",
      filename: "Board meeting.pdf",
    });
  });

  it("previews the filename it is going to use", async () => {
    const user = userEvent.setup();
    created();
    open();

    await user.type(screen.getByPlaceholderText("Untitled"), "Q3 report");
    expect(screen.getByText("Q3 report.pdf")).toBeInTheDocument();
  });

  it("falls back to Untitled.pdf when no title is given", async () => {
    const user = userEvent.setup();
    const create = created();
    open();

    await user.type(screen.getByRole("textbox", { name: /Content/ }), "Just body text.");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0].filename).toBe("Untitled.pdf");
  });

  it("will not create an empty document", () => {
    created();
    open();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });
});

describe("blank pages", () => {
  it("sends the page count instead of any content", async () => {
    const user = userEvent.setup();
    const create = created();
    open();

    await user.click(screen.getByRole("tab", { name: "Blank pages" }));
    const count = screen.getByRole("spinbutton");
    await user.clear(count);
    await user.type(count, "5");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({ blank_pages: 5, content: "" });
  });

  it("is available with nothing typed at all", async () => {
    const user = userEvent.setup();
    created();
    open();

    await user.click(screen.getByRole("tab", { name: "Blank pages" }));
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
  });

  it("clamps a page count outside what the server accepts", async () => {
    const user = userEvent.setup();
    const create = created();
    open();

    await user.click(screen.getByRole("tab", { name: "Blank pages" }));
    const count = screen.getByRole("spinbutton");
    await user.clear(count);
    await user.type(count, "9999");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0].blank_pages).toBe(200);
  });
});

describe("page size", () => {
  it("passes A4 through when it is chosen", async () => {
    const user = userEvent.setup();
    const create = created();
    open();

    await user.type(screen.getByRole("textbox", { name: /Content/ }), "Body.");
    await user.selectOptions(screen.getByRole("combobox"), "a4");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0].page_size).toBe("a4");
  });
});

describe("outcomes", () => {
  it("hands the new document back so it can be opened", async () => {
    const user = userEvent.setup();
    created();
    const onCreated = vi.fn();
    open({ onCreated });

    await user.type(screen.getByRole("textbox", { name: /Content/ }), "Body.");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new1", "Notes.pdf"));
  });

  it("reports a failure and stays open so the text is not lost", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "createDocument").mockRejectedValue(new Error("Server said no"));
    const onCreated = vi.fn();
    open({ onCreated });

    await user.type(screen.getByRole("textbox", { name: /Content/ }), "Hard-won prose.");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith("Server said no", "error"));
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("Hard-won prose.")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    open({ onClose });

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("reaching it from the library", () => {
  it("offers New document beside Upload", async () => {
    vi.spyOn(api, "documents").mockResolvedValue({ items: [], total: 0 } as never);
    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);

    expect(await screen.findByRole("button", { name: "New document" }))
      .toBeInTheDocument();
  });

  it("opens the dialog when it is pressed", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "documents").mockResolvedValue({ items: [], total: 0 } as never);
    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);

    await user.click(await screen.findByRole("button", { name: "New document" }));
    expect(screen.getByRole("dialog", { name: "New document" }))
      .toBeInTheDocument();
  });

  it("accepts more than PDF in the file picker", async () => {
    vi.spyOn(api, "documents").mockResolvedValue({ items: [], total: 0 } as never);
    const { container } = render(
      <Library workspace={workspace} onOpen={() => {}} notify={notify} />);

    await screen.findByRole("button", { name: "New document" });
    const accept = container.querySelector<HTMLInputElement>(
      'input[type="file"]')?.accept ?? "";
    for (const kind of [".docx", ".xlsx", ".pptx", ".txt", ".csv", ".png", "application/pdf"]) {
      expect(accept).toContain(kind);
    }
  });
});

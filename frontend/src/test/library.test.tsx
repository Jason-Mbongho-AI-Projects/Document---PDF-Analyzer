/**
 * Library tests, focused on the destructive path.
 *
 * Deletion removes every version and every stored byte, so the thing worth
 * proving is that it cannot happen by accident.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api, type DocumentSummary } from "../api";
import { Library } from "../views/Library";

const workspace = { id: "ws1", name: "Development", description: null };
const notify = vi.fn();

function doc(id: string, filename: string, archived = false): DocumentSummary {
  return {
    id, filename, workspace_id: "ws1", size_bytes: 2048,
    status: "ready", page_count: 3, is_archived: archived,
    created_at: "2026-08-15T10:00:00Z",
  };
}

function listing(items: DocumentSummary[]) {
  return vi.spyOn(api, "documents").mockResolvedValue({
    items, total: items.length,
  } as never);
}

beforeEach(() => notify.mockClear());

describe("listing", () => {
  it("shows each document with an open, archive and delete action", async () => {
    listing([doc("d1", "contract.pdf")]);
    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);

    expect(await screen.findByRole("button", { name: "contract.pdf" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("opens the document when the filename is clicked", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={onOpen} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "contract.pdf" }));

    expect(onOpen).toHaveBeenCalledWith("d1");
  });
});

describe("deleting", () => {
  it("never deletes on the first click", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined as never);
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Delete" }));

    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();
  });

  it("names what is about to be destroyed", async () => {
    const user = userEvent.setup();
    listing([doc("d1", "quarterly-report.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Delete" }));

    const dialog = screen.getByText(/Delete 1 document\?/)
      .closest<HTMLElement>(".modal")!;
    expect(within(dialog).getByText("quarterly-report.pdf")).toBeInTheDocument();
  });

  it("deletes only after explicit confirmation", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined as never);
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: /Yes, delete permanently/ }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("d1"));
  });

  it("cancelling deletes nothing", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined as never);
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(remove).not.toHaveBeenCalled();
    expect(screen.queryByText(/cannot be undone/i)).not.toBeInTheDocument();
  });

  it("offers archiving as the reversible alternative", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined as never);
    const archive = vi.spyOn(api, "archiveDocument").mockResolvedValue({} as never);
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Archive instead" }));

    await waitFor(() => expect(archive).toHaveBeenCalledWith("d1"));
    expect(remove).not.toHaveBeenCalled();
  });
});

describe("bulk actions", () => {
  it("deletes every selected document after one confirmation", async () => {
    const user = userEvent.setup();
    const remove = vi.spyOn(api, "deleteDocument").mockResolvedValue(undefined as never);
    listing([doc("d1", "a.pdf"), doc("d2", "b.pdf"), doc("d3", "c.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await screen.findByRole("button", { name: "a.pdf" });

    await user.click(screen.getByLabelText("Select a.pdf"));
    await user.click(screen.getByLabelText("Select c.pdf"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(within(
      screen.getByText("2 selected").closest<HTMLElement>(".selection-bar")!,
    ).getByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: /Yes, delete permanently/ }));

    await waitFor(() => expect(remove).toHaveBeenCalledTimes(2));
    expect(remove).toHaveBeenCalledWith("d1");
    expect(remove).toHaveBeenCalledWith("d3");
    expect(remove).not.toHaveBeenCalledWith("d2");
  });

  it("clearing the selection hides the bulk bar", async () => {
    const user = userEvent.setup();
    listing([doc("d1", "a.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await screen.findByRole("button", { name: "a.pdf" });

    await user.click(screen.getByLabelText("Select a.pdf"));
    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });
});

describe("archiving", () => {
  it("archives from the card without any confirmation", async () => {
    const user = userEvent.setup();
    const archive = vi.spyOn(api, "archiveDocument").mockResolvedValue({} as never);
    listing([doc("d1", "contract.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await user.click(await screen.findByRole("button", { name: "Archive" }));

    await waitFor(() => expect(archive).toHaveBeenCalledWith("d1"));
  });

  it("shows Restore for an archived document", async () => {
    listing([doc("d1", "old.pdf", true)]);
    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);

    expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
    expect(screen.getByText("archived")).toBeInTheDocument();
  });

  it("asks the server for archived documents when the filter is on", async () => {
    const user = userEvent.setup();
    const list = listing([doc("d1", "a.pdf")]);

    render(<Library workspace={workspace} onOpen={() => {}} notify={notify} />);
    await screen.findByRole("button", { name: "a.pdf" });

    await user.click(screen.getByLabelText(/Show archived/i));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith("ws1", "", true));
  });
});

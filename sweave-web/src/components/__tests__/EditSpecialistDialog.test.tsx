/**
 * EditSpecialistDialog tests.
 *
 * Pins: fields prefill from the record; save PUTs the trimmed
 * description/prompt/role with the record's scope and notifies;
 * API failure notifies an error and keeps the dialog open.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EditSpecialistDialog } from "@/components/EditSpecialistDialog";
import { api } from "@/api/client";
import type { SpecialistSummary } from "@/types";

vi.mock("@/api/client", () => ({
  api: { updateSpecialist: vi.fn() },
}));

const pushNotification = vi.fn();
vi.mock("@/context/AppProvider", () => ({
  useApp: () => ({ pushNotification }),
}));

const updateMock = vi.mocked(api.updateSpecialist);

function specialist(overrides?: Partial<SpecialistSummary>): SpecialistSummary {
  return {
    name: "alpha",
    scope: "project",
    is_orchestrator: false,
    role_ref: "backend",
    description: "Old description",
    system_prompt: "Old prompt with {{task}} var",
    harness: "opencode",
    current_model: null,
    session_id: null,
    ...overrides,
  };
}

function renderDialog(spec: SpecialistSummary | null = specialist()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <EditSpecialistDialog specialist={spec} open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("EditSpecialistDialog", () => {
  it("prefills description, role ref, and prompt from the record", () => {
    renderDialog();
    expect((screen.getByLabelText(/description/i) as HTMLInputElement).value).toBe(
      "Old description",
    );
    expect((screen.getByLabelText(/role ref/i) as HTMLInputElement).value).toBe("backend");
    expect((screen.getByLabelText(/system prompt/i) as HTMLTextAreaElement).value).toBe(
      "Old prompt with {{task}} var",
    );
  });

  it("saves trimmed values with the record scope and notifies", async () => {
    updateMock.mockResolvedValue(specialist());
    const { onOpenChange } = renderDialog();
    fireEvent.change(screen.getByLabelText(/description/i), {
      target: { value: "  New description  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith(
        "alpha",
        {
          description: "New description",
          system_prompt: "Old prompt with {{task}} var",
          role_ref: "backend",
        },
        "project",
      ),
    );
    expect(pushNotification).toHaveBeenCalledWith(
      "success",
      expect.stringContaining("alpha"),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("passes global scope for global records", async () => {
    updateMock.mockResolvedValue(specialist({ scope: "global" }));
    renderDialog(specialist({ scope: "global" }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith("alpha", expect.anything(), "global"),
    );
  });

  it("keeps the dialog open and notifies on API failure", async () => {
    updateMock.mockRejectedValue(new Error("nope"));
    const { onOpenChange } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(pushNotification).toHaveBeenCalledWith("error", expect.stringContaining("nope")),
    );
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

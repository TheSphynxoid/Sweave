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
import { EditSpecialistDialog, specialistEditBody } from "@/components/EditSpecialistDialog";
import { api } from "@/api/client";
import type { SpecialistSummary } from "@/types";

vi.mock("@/api/client", () => ({
  api: {
    updateSpecialist: vi.fn(),
    listHarnesses: vi.fn().mockResolvedValue([
      { name: "sweave-engine", display_name: "Sweave Engine" },
      { name: "opencode", display_name: "OpenCode" },
    ]),
  },
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
          harness: "opencode",
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

  it("keeps the dialog open and notifies on API failure", async () => {    updateMock.mockRejectedValue(new Error("nope"));
    const { onOpenChange } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(pushNotification).toHaveBeenCalledWith("error", expect.stringContaining("nope")),
    );
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("shows the current harness on the picker (change = next delegation)", async () => {    renderDialog();
    // NOTE: the Radix Select popover itself is not opened here —
    // bare Radix popovers hang jsdom (see docs/GOTCHAS.md). The
    // save test above pins that the harness value rides the PUT.
    expect((await screen.findByTestId("spec-edit-harness")).textContent).toContain(
      "opencode",
    );
    // Once the harnesses query resolves, the stored value matches a
    // listed harness and shows its display name.
    await waitFor(() =>
      expect(screen.getByTestId("spec-edit-harness").textContent).toContain("OpenCode"),
    );
  });

  it("locks prompt fields in seed mode and disables save when unchanged", async () => {
    renderDialog(specialist({ scope: "seed", harness: "sweave-engine" }));
    expect((screen.getByLabelText(/description/i) as HTMLInputElement).hasAttribute("disabled")).toBe(true);
    expect((screen.getByLabelText(/role ref/i) as HTMLInputElement).hasAttribute("disabled")).toBe(true);
    expect((screen.getByLabelText(/system prompt/i) as HTMLTextAreaElement).hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("spec-edit-harness")).toBeTruthy();
    const save = screen.getByRole("button", { name: /save changes/i }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(updateMock).not.toHaveBeenCalled();
  });
});

describe("specialistEditBody", () => {
  const fields = {
    description: "D",
    systemPrompt: "P",
    roleRef: "backend",
    harness: "sweave-engine",
  };
  it("sends the full patch for project/global records", () => {
    expect(
      specialistEditBody(
        specialist({ scope: "project", harness: "opencode" }),
        fields,
      ),
    ).toEqual({
      description: "D",
      system_prompt: "P",
      role_ref: "backend",
      harness: "sweave-engine",
    });
  });

  it("sends harness-only for seeds (prompt edits would 400)", () => {
    expect(
      specialistEditBody(specialist({ scope: "seed" }), fields),
    ).toEqual({ harness: "sweave-engine" });
  });
});

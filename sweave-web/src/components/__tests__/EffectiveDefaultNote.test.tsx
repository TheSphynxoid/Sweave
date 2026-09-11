/**
 * EffectiveDefaultNote tests (M1.13 step 3).
 *
 * Rendered only when the specialist has no explicit current_model
 * and a global default exists; it names the default the specialist
 * effectively runs on.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { EffectiveDefaultNote } from "../EffectiveDefaultNote";

describe("EffectiveDefaultNote", () => {
  it("hidden when the specialist has an explicit current_model", () => {
    const { container } = render(
      <EffectiveDefaultNote currentModel="opencode/glm-5.3+max" defaultModel="ollama/b-model" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the default when no current_model is set", () => {
    render(
      <EffectiveDefaultNote currentModel={null} defaultModel="opencode/glm-5.3-flash" testId="note-x" />,
    );
    const note = screen.getByTestId("note-x");
    expect(note.textContent).toContain("opencode/glm-5.3-flash");
  });

  it("hidden when there is no default at all", () => {
    const { container } = render(
      <EffectiveDefaultNote currentModel={null} defaultModel={null} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

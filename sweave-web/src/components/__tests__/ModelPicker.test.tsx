/**
 * ModelPicker panel tests.
 *
 * The popover shell is browser-only (a bare Radix popover hangs
 * jsdom — see docs/GOTCHAS.md), so these mount `ModelPickerPanel`
 * directly, the same split as `PathPickerBrowser`. The shell is
 * covered by `scripts/ui-model-probe.mjs`.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  MODEL_PICKER_MAX_SHOWN,
  ModelPickerPanel,
  filterModelOptions,
  splitModelId,
} from "@/components/ModelPicker";

const OPTIONS = [
  "ollama/qwen3:8b",
  "opencode/glm-5.3",
  "opencode/gpt-5",
  "gmi/MiniMaxAI/MiniMax-M3",
];

describe("filterModelOptions", () => {
  it("returns everything on an empty query", () => {
    expect(filterModelOptions(OPTIONS, "")).toEqual(OPTIONS);
    expect(filterModelOptions(OPTIONS, "   ")).toEqual(OPTIONS);
  });

  it("matches case-insensitively across provider and id", () => {
    expect(filterModelOptions(OPTIONS, "GLM")).toEqual(["opencode/glm-5.3"]);
    expect(filterModelOptions(OPTIONS, "ollama")).toEqual(["ollama/qwen3:8b"]);
    expect(filterModelOptions(OPTIONS, "open")).toEqual([
      "opencode/glm-5.3",
      "opencode/gpt-5",
    ]);
  });

  it("returns an empty list when nothing matches", () => {
    expect(filterModelOptions(OPTIONS, "zzz-nope")).toEqual([]);
  });
});

describe("splitModelId", () => {
  it("splits provider from id", () => {
    expect(splitModelId("opencode/glm-5.3")).toEqual({
      provider: "opencode",
      model: "glm-5.3",
    });
  });

  it("leaves bare ids whole", () => {
    expect(splitModelId("gmi")).toEqual({ provider: null, model: "gmi" });
  });
});

function renderPanel(props?: Partial<Parameters<typeof ModelPickerPanel>[0]>) {
  const onSelect = vi.fn();
  render(
    <ModelPickerPanel
      options={OPTIONS}
      value=""
      onSelect={onSelect}
      testId="model-picker"
      {...props}
    />,
  );
  return { onSelect };
}

describe("ModelPickerPanel", () => {
  it("lists every option with a total count", () => {
    renderPanel();
    expect(screen.getByTestId("model-picker-list").querySelectorAll('[role="option"]')).toHaveLength(4);
    expect(screen.getByTestId("model-picker-count").textContent).toBe("4 of 4 models");
  });

  it("filters the list as the user types", () => {
    renderPanel();
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "glm" },
    });
    const rows = screen.getByTestId("model-picker-list").querySelectorAll('[role="option"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("glm-5.3");
    expect(screen.getByTestId("model-picker-count").textContent).toBe("1 of 4 models");
  });

  it("shows an empty state when nothing matches", () => {
    renderPanel();
    fireEvent.change(screen.getByTestId("model-picker-search"), {
      target: { value: "zzz-nope" },
    });
    expect(screen.getByTestId("model-picker-list").querySelectorAll('[role="option"]')).toHaveLength(0);
    expect(screen.getByText(/No models match/)).toBeTruthy();
  });

  it("calls onSelect with the clicked model", () => {
    const { onSelect } = renderPanel();
    fireEvent.click(screen.getByRole("option", { name: /gpt-5/ }));
    expect(onSelect).toHaveBeenCalledWith("opencode/gpt-5");
  });

  it("marks the current value as selected", () => {
    renderPanel({ value: "ollama/qwen3:8b" });
    expect(
      screen.getByRole("option", { name: /qwen3:8b/ }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("caps the rendered rows and says how many match", () => {
    const many = Array.from({ length: MODEL_PICKER_MAX_SHOWN + 30 }, (_, i) => `opencode/m-${i}`);
    renderPanel({ options: many });
    expect(
      screen.getByTestId("model-picker-list").querySelectorAll('[role="option"]'),
    ).toHaveLength(MODEL_PICKER_MAX_SHOWN);
    expect(screen.getByTestId("model-picker-count").textContent).toBe(
      `Showing ${MODEL_PICKER_MAX_SHOWN} of ${many.length} matches — refine the search`,
    );
  });
});

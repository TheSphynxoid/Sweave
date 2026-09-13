/**
 * Effort selection tests.
 *
 * The Radix Select popover cannot be opened in jsdom (bare Radix
 * popover hangs the worker — see docs/GOTCHAS.md), so these pin
 * the pure combine/split contract plus mount-only rendering
 * (effort trigger visible iff the model advertises variants).
 * No test clicks a popover trigger.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  EffortSelect,
  ModelWithEffort,
  effortBaseOf,
  nextModelValue,
  withEffort,
} from "@/components/EffortSelect";

const VARIANTS = {
  "openrouter/thinkingmachines/inkling:free": ["high", "low", "max", "medium", "minimal", "none"],
  "ollama/qwen3:8b": [],
};

describe("effortBaseOf / withEffort", () => {
  it("strips and sets the +variant suffix", () => {
    expect(effortBaseOf("openrouter/thinkingmachines/inkling:free+low")).toBe(
      "openrouter/thinkingmachines/inkling:free",
    );
    expect(effortBaseOf("ollama/qwen3:8b")).toBe("ollama/qwen3:8b");
    expect(withEffort("ollama/qwen3:8b", "low")).toBe("ollama/qwen3:8b+low");
    expect(
      withEffort("openrouter/thinkingmachines/inkling:free+low", "high"),
    ).toBe("openrouter/thinkingmachines/inkling:free+high");
    expect(
      withEffort("openrouter/thinkingmachines/inkling:free+low", null),
    ).toBe("openrouter/thinkingmachines/inkling:free");
  });
});

describe("nextModelValue", () => {
  it("keeps the effort iff the new model advertises it", () => {
    expect(
      nextModelValue(
        "openrouter/thinkingmachines/inkling:free",
        "openrouter/thinkingmachines/inkling:free+low",
        VARIANTS,
      ),
    ).toBe("openrouter/thinkingmachines/inkling:free+low");
  });

  it("drops the effort when the new model lacks it", () => {
    expect(
      nextModelValue(
        "ollama/qwen3:8b",
        "openrouter/thinkingmachines/inkling:free+low",
        VARIANTS,
      ),
    ).toBe("ollama/qwen3:8b");
  });

  it("passes through models without a suffix", () => {
    expect(nextModelValue("ollama/qwen3:8b", "", VARIANTS)).toBe("ollama/qwen3:8b");
  });
});

describe("EffortSelect", () => {
  it("renders nothing when the model has no variants", () => {
    const { container } = render(
      <EffortSelect value={null} variants={[]} onChange={vi.fn()} testId="effort" />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("effort")).toBeNull();
  });

  it("renders the current effort in the trigger", () => {
    render(
      <EffortSelect
        value="low"
        variants={["high", "low"]}
        onChange={vi.fn()}
        testId="effort"
      />,
    );
    const trigger = screen.getByTestId("effort");
    expect(trigger.textContent).toContain("low");
  });
});

describe("ModelWithEffort", () => {
  const options = ["openrouter/thinkingmachines/inkling:free", "ollama/qwen3:8b"];

  it("shows the effort dropdown for a model with variants", () => {
    render(
      <ModelWithEffort
        value="openrouter/thinkingmachines/inkling:free+low"
        onValueChange={vi.fn()}
        options={options}
        variantsMap={VARIANTS}
        testId="model-effort"
      />,
    );
    expect(screen.getByTestId("model-effort-effort").textContent).toContain("low");
  });

  it("hides the effort dropdown for a model without variants", () => {
    render(
      <ModelWithEffort
        value="ollama/qwen3:8b"
        onValueChange={vi.fn()}
        options={options}
        variantsMap={VARIANTS}
        testId="model-effort"
      />,
    );
    expect(screen.queryByTestId("model-effort-effort")).toBeNull();
  });

  it("hides the effort dropdown when the variants map is missing", () => {
    render(
      <ModelWithEffort
        value="openrouter/thinkingmachines/inkling:free"
        onValueChange={vi.fn()}
        options={options}
        testId="model-effort"
      />,
    );
    expect(screen.queryByTestId("model-effort-effort")).toBeNull();
  });
});

describe("ModelPicker trigger + provider caption (closed shell)", () => {
  const options = ["opencode/moonshotai/kimi-k2.5", "ollama/qwen3:8b"];

  it("shows the model half in the trigger, provider on its own caption line", () => {
    render(
      <ModelWithEffort
        value="opencode/moonshotai/kimi-k2.5"
        onValueChange={vi.fn()}
        options={options}
        testId="model-cap"
      />,
    );
    const trigger = screen.getByTestId("model-cap");
    expect(trigger.textContent).toContain("moonshotai/kimi-k2.5");
    expect(trigger.textContent).not.toContain("opencode/");
    expect(screen.getByTestId("model-cap-provider").textContent).toBe("via opencode");
  });

  it("shows no caption for a bare id or an empty value", () => {
    const { rerender } = render(
      <ModelWithEffort
        value="qwen3:8b"
        onValueChange={vi.fn()}
        options={options}
        testId="model-cap"
      />,
    );
    expect(screen.getByTestId("model-cap").textContent).toContain("qwen3:8b");
    expect(screen.queryByTestId("model-cap-provider")).toBeNull();
    rerender(
      <ModelWithEffort
        value=""
        onValueChange={vi.fn()}
        options={options}
        testId="model-cap"
      />,
    );
    expect(screen.queryByTestId("model-cap-provider")).toBeNull();
  });

  it("levels the effort trigger with the picker via effortClassName", () => {
    render(
      <ModelWithEffort
        value="openrouter/thinkingmachines/inkling:free+low"
        onValueChange={vi.fn()}
        options={["openrouter/thinkingmachines/inkling:free", "ollama/qwen3:8b"]}
        variantsMap={VARIANTS}
        effortClassName="h-8 text-xs"
        testId="model-level"
      />,
    );
    const trigger = screen.getByTestId("model-level-effort");
    expect(trigger.className).toContain("h-8");
    expect(trigger.className).not.toContain("h-10");
  });
});

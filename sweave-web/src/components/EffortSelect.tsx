"use client";

/**
 * Effort selection for reasoning-capable models.
 *
 * Variants are a per-turn dimension, not separate registry entries:
 * the model picker lists each model once, and this dropdown (shown
 * only when the selected model advertises variants) picks the
 * reasoning-effort suffix (`model+low`). The combined string is the
 * stored contract everywhere (overrides, defaults, wire) — this
 * control only edits the suffix.
 */
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ModelPicker, splitModelVariant } from "@/components/ModelPicker";
import { cn } from "@/utils/cn";

/** Strip any +variant suffix -> the registry base id. */
export function effortBaseOf(modelId: string): string {
  const { variant } = splitModelVariant(modelId);
  return variant ? modelId.slice(0, -(variant.length + 1)) : modelId;
}

/** Set (or clear, with null) the +variant suffix on a model id. */
export function withEffort(modelId: string, effort: string | null): string {
  const base = effortBaseOf(modelId);
  return effort ? `${base}+${effort}` : base;
}

/**
 * Next combined value after picking a (base) model: keep the current
 * effort suffix only when the newly picked model advertises it,
 * otherwise drop it. Pure so the keep/drop contract is unit-tested
 * (the picker popover itself can't be clicked in jsdom).
 */
export function nextModelValue(
  pickedBase: string,
  currentValue: string,
  variantsMap: Record<string, string[]> = {},
): string {
  const { variant } = splitModelVariant(currentValue);
  const advertised = variantsMap[pickedBase] ?? [];
  const keep = variant && advertised.includes(variant) ? variant : null;
  return keep ? `${pickedBase}+${keep}` : pickedBase;
}

export interface EffortSelectProps {
  /** Currently selected effort, or null for the provider default. */
  value: string | null;
  /** Advertised efforts for the selected model (empty hides the control). */
  variants: string[];
  onChange: (effort: string | null) => void;
  testId?: string;
  className?: string;
  disabled?: boolean;
}

export function EffortSelect({
  value,
  variants,
  onChange,
  testId,
  className,
  disabled,
}: EffortSelectProps) {
  if (variants.length === 0) return null;
  return (
    <Select
      value={value ?? "default"}
      onValueChange={(v) => onChange(v === "default" ? null : v)}
      disabled={disabled}
    >
      <SelectTrigger
        data-testid={testId}
        aria-label="Reasoning effort"
        title="Reasoning effort for this model (provider default when unset)"
        className={cn("w-32 shrink-0 font-mono text-xs [&>span]:truncate", className)}
      >
        <SelectValue placeholder="Effort" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="default">Default</SelectItem>
        {variants.map((v) => (
          <SelectItem key={v} value={v}>
            {v}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export interface ModelWithEffortProps {
  /** Combined `model[+variant]` value (the stored contract). */
  value: string;
  onValueChange: (model: string) => void;
  options: string[];
  /** Variants per qualified model id, from GET /api/models. */
  variantsMap?: Record<string, string[]>;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** Extra classes for the effort trigger (match the picker height, e.g. "h-8 text-xs"). */
  effortClassName?: string;
  testId?: string;
}

/**
 * Model picker + effort dropdown as one control. Changing the model
 * keeps the effort suffix only when the new model advertises it;
 * changing the effort rewrites just the suffix. Models without
 * advertised variants render the picker alone.
 */
export function ModelWithEffort({
  value,
  onValueChange,
  options,
  variantsMap = {},
  placeholder,
  disabled,
  className,
  effortClassName,
  testId,
}: ModelWithEffortProps) {
  const { variant } = splitModelVariant(value);
  const base = effortBaseOf(value);
  const efforts = variantsMap[base] ?? [];

  const handleModel = (m: string) => {
    onValueChange(nextModelValue(m, value, variantsMap));
  };

  const handleEffort = (effort: string | null) => {
    onValueChange(withEffort(value, effort));
  };

  return (
    // items-start: tops stay level — the provider caption flows under
    // the picker button without pushing the effort trigger down. The
    // caller matches heights via className/effortClassName (twMerge
    // lets the caller's h-* win over each control's default).
    <div className="flex min-w-0 flex-1 items-start gap-2">
      <ModelPicker
        value={base}
        onValueChange={handleModel}
        options={options}
        placeholder={placeholder}
        disabled={disabled}
        className={cn("min-w-0 flex-1", className)}
        testId={testId}
      />
      <EffortSelect
        value={variant}
        variants={efforts}
        onChange={handleEffort}
        disabled={disabled}
        className={effortClassName}
        testId={testId ? `${testId}-effort` : undefined}
      />
    </div>
  );
}

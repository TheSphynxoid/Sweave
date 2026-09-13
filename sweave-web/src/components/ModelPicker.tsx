"use client";

/**
 * Searchable model picker.
 *
 * The global registry holds 600+ `provider/model` ids — a plain
 * dropdown is unusable, so every model surface (specialist cards,
 * the create dialog, the Settings default) shares this combobox:
 * a trigger button + a popover panel with a search field over a
 * capped, scrollable list.
 *
 * Test split (see docs/GOTCHAS.md "bare Radix popover hangs jsdom"):
 * `ModelPickerPanel` is the testable panel (mount it directly in
 * vitest); `ModelPicker` is the popover shell, verified via the
 * screenshot probe (`scripts/ui-model-probe.mjs`).
 */
import { useMemo, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/utils/cn";

/** Max rows rendered in the list; the footer says how many match. */
export const MODEL_PICKER_MAX_SHOWN = 120;

/** Case-insensitive substring match over the qualified id. */
export function filterModelOptions(options: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  return options.filter((m) => m.toLowerCase().includes(q));
}

/** Split `provider/model` for the dimmed-provider row rendering. */
export function splitModelId(id: string): { provider: string | null; model: string } {
  const slash = id.indexOf("/");
  if (slash <= 0) return { provider: null, model: id };
  return { provider: id.slice(0, slash), model: id.slice(slash + 1) };
}

/**
 * Split an optional `+variant` suffix off a qualified model id
 * (mirrors `parse_model_ref` on the backend: last `+`, single
 * token tail). Variants select a reasoning-effort preset per turn
 * (e.g. `.../inkling:free+low`); the picker renders the variant
 * as a badge so suffixed entries read as what they are.
 */
export function splitModelVariant(id: string): { base: string; variant: string | null } {
  const plus = id.lastIndexOf("+");
  if (plus <= 0) return { base: id, variant: null };
  const tail = id.slice(plus + 1);
  if (!tail || tail.includes("/")) return { base: id, variant: null };
  return { base: id.slice(0, plus), variant: tail };
}

export interface ModelPickerPanelProps {
  options: string[];
  value: string;
  onSelect: (model: string) => void;
  testId?: string;
}

/**
 * A `provider/model` id rendered so the MODEL half survives narrow
 * containers (panel rows): the dimmed provider prefix shrinks first
 * (`flex-shrink: 3` vs the default 1), the model tail truncates last.
 * Long ids read `openc…/kimi-k2.5` instead of losing the distinctive
 * tail — pair with `title={fullId}` on the interactive parent for
 * hover reveal. Pure spans, unit-tested directly. (The closed trigger
 * below deliberately does NOT use this: it shows the model half only
 * and puts the provider on its own caption line.)
 */
export function ModelIdText({ id }: { id: string }) {
  const { provider, model } = splitModelId(id);
  return (
    <span className="flex min-w-0 flex-1 items-center overflow-hidden whitespace-nowrap">
      {provider && (
        <span className="truncate text-muted-foreground [flex-shrink:3]">{provider}/</span>
      )}
      <span className="truncate">{model}</span>
    </span>
  );
}

export function ModelPickerPanel({ options, value, onSelect, testId }: ModelPickerPanelProps) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => filterModelOptions(options, query), [options, query]);
  const shown = filtered.slice(0, MODEL_PICKER_MAX_SHOWN);
  const searchId = testId ? `${testId}-search` : undefined;
  const listId = testId ? `${testId}-list` : undefined;

  return (
    <div className="flex max-h-[var(--radix-popper-available-height)] flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border px-2 py-1.5">
        <Search size={13} className="shrink-0 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && filtered.length > 0) {
              e.preventDefault();
              onSelect(filtered[0]);
            }
          }}
          placeholder="Search models…"
          data-testid={searchId}
          className="h-7 w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
        />
      </div>
      <div
        role="listbox"
        data-testid={listId}
        className="min-h-0 flex-1 overflow-y-auto p-1"
      >
        {shown.map((m) => {
          const { provider, model } = splitModelId(m);
          const { variant } = splitModelVariant(m);
          // The model half may carry the +variant suffix; strip it
          // for display (the badge below shows it instead).
          const modelBase = variant ? model.slice(0, model.length - variant.length - 1) : model;
          const displayId = provider ? `${provider}/${modelBase}` : modelBase;
          const selected = m === value;
          return (
            <button
              key={m}
              type="button"
              role="option"
              aria-selected={selected}
              title={m}
              onClick={() => onSelect(m)}
              className={cn(
                "relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-left font-mono text-xs outline-none hover:bg-accent hover:text-accent-foreground",
                selected && "bg-accent/60",
              )}
            >
              <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
                {selected && <Check size={13} />}
              </span>
              <ModelIdText id={displayId} />
              {variant && (
                <span className="ml-1.5 shrink-0 rounded bg-primary/15 px-1 py-px font-mono text-[10px] text-primary">
                  {variant}
                </span>
              )}
            </button>
          );
        })}
        {filtered.length === 0 && (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            No models match “{query.trim()}”.
          </p>
        )}
      </div>
      <p data-testid={testId ? `${testId}-count` : undefined} className="shrink-0 border-t border-border px-2 py-1 text-[10px] text-muted-foreground">
        {filtered.length > MODEL_PICKER_MAX_SHOWN
          ? `Showing ${MODEL_PICKER_MAX_SHOWN} of ${filtered.length} matches — refine the search`
          : `${filtered.length} of ${options.length} models`}
      </p>
    </div>
  );
}

export interface ModelPickerProps {
  value: string;
  onValueChange: (model: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  testId?: string;
}

export function ModelPicker({
  value,
  onValueChange,
  options,
  placeholder = "Select model",
  disabled,
  className,
  testId,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  // The trigger shows the MODEL half only (full width for the
  // distinctive tail); the provider gets its own legible caption line
  // under the button instead of sharing the truncated text area.
  const { provider, model } = value ? splitModelId(value) : { provider: null, model: "" };

  return (
    <div className="min-w-0 flex-1">
      <Popover
        open={open}
        onOpenChange={(o) => setOpen(o)}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            data-testid={testId}
            title={value || placeholder}
            className={cn(
              "flex h-10 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 font-mono text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
              !value && "text-muted-foreground",
              className,
            )}
          >
            <span className="min-w-0 truncate">{value ? model : placeholder}</span>
            <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          sideOffset={4}
          className="w-[var(--radix-popover-trigger-width)] min-w-56 p-0"
        >
          <ModelPickerPanel
            options={options}
            value={value}
            onSelect={(m) => {
              onValueChange(m);
              setOpen(false);
            }}
            testId={testId}
          />
        </PopoverContent>
      </Popover>
      {provider && (
        <p
          data-testid={testId ? `${testId}-provider` : undefined}
          className="mt-1 truncate pl-px font-mono text-[10px] text-muted-foreground"
        >
          via {provider}
        </p>
      )}
    </div>
  );
}

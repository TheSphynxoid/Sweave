"use client";

/**
 * EffectiveDefaultNote (M1.13 step 3, ruling 2026-09-10).
 *
 * The specialist surface shows the CURRENT model plus the EFFECTIVE
 * default: when a specialist has no explicit ``current_model`` it
 * runs on the global default (models.yaml / GET /api/models
 * ``default``), and the card says so instead of showing an empty
 * picker value.
 */
export interface EffectiveDefaultNoteProps {
  /** The specialist's explicit current_model ("provider/model[+variant]"). */
  currentModel: string | null | undefined;
  /** The global default model (GET /api/models `default`). */
  defaultModel: string | null | undefined;
  testId?: string;
}

export function EffectiveDefaultNote({
  currentModel,
  defaultModel,
  testId,
}: EffectiveDefaultNoteProps) {
  if (currentModel) return null;
  if (!defaultModel) return null;
  return (
    <p
      data-testid={testId ?? "effective-default-note"}
      className="ml-14 text-[10px] text-muted-foreground"
    >
      Starting on default{" "}
      <span className="font-mono">{defaultModel}</span>
    </p>
  );
}

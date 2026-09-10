/**
 * Copy-id affordances (debug slice).
 *
 * Sessions (`Sweave-…-1e451c`), chat-turn delegations (`chat-…`) and
 * message rows (`7a2cd4de`) each carry a different id, and debugging
 * ("which turn timed out?") means quoting the right one. These two
 * tiny components make every surfaced id click-to-copy:
 *
 * - `CopyIdBadge` — the sliced-id badge used on delegation ids in the
 *   thread (full id in `title`, click copies the full id).
 * - `CopyIconButton` — icon-only copy used next to the session name in
 *   the Topbar (session ids are long; only the icon shows, the full id
 *   is the `title`).
 *
 * Clipboard failures are silent (insecure contexts have no clipboard),
 * matching the existing `CopyCodeButton` contract.
 */
import { useCallback, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/utils/cn";

function useCopyText(id: string) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    if (!id) return;
    void navigator.clipboard
      .writeText(id)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {
        // clipboard may be unavailable (insecure context); silent.
      });
  }, [id]);
  return { copied, copy };
}

export function CopyIdBadge({
  id,
  label,
  testId,
  className,
}: {
  id: string;
  label: string;
  testId: string;
  className?: string;
}) {
  const { copied, copy } = useCopyText(id);
  return (
    <button
      type="button"
      onClick={copy}
      title={`${label} ${id} — click to copy`}
      aria-label={copied ? "Copied" : `Copy ${label}`}
      data-testid={testId}
      data-copied={copied}
      className={cn(
        "rounded border border-border bg-muted px-1.5 py-px font-mono text-[10px] text-muted-foreground transition-colors hover:text-foreground",
        className,
      )}
    >
      {id.slice(0, 8)}
    </button>
  );
}

export function CopyIconButton({
  id,
  label,
  testId,
  className,
}: {
  id: string;
  label: string;
  testId: string;
  className?: string;
}) {
  const { copied, copy } = useCopyText(id);
  return (
    <button
      type="button"
      onClick={copy}
      title={`${label} ${id} — click to copy`}
      aria-label={copied ? "Copied" : `Copy ${label}`}
      data-testid={testId}
      data-copied={copied}
      className={cn(
        "shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        className,
      )}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}

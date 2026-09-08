/**
 * Assistant text part (R4.2 step 2a).
 *
 * Wraps the assistant message's text content in the ``Markdown``
 * renderer. Used to render markdown + GFM + Shiki-highlighted
 * code blocks for assistant messages.
 *
 * Streaming safety: the Markdown component is itself try/catch-
 * wrapped (see ``Markdown``). On a partial / unclosed markdown
 * fence mid-stream, it falls back to plain text rather than
 * throwing and tearing the thread tree down. The M1.8 invariant
 * is preserved.
 *
 * Accepts either:
 * - Direct `text` prop: { text: string }
 * - assistant-ui part props: { text: string, status, ... } or { part: { text: string } }
 */
import { Markdown } from "./Markdown";

interface AssistantTextPartProps {
  type?: string;
  text?: string;
  // assistant-ui TextMessagePartProps status (PartState: running/complete/
  // incomplete + reason) — the renderer only needs a loose view of it.
  status?: { type: string; reason?: string } | undefined;
  // assistant-ui part props shape
  part?: { text: string; status?: { type: string } };
}

export function AssistantTextPart({ text, part, status }: AssistantTextPartProps) {
  // Priority: direct text prop > part.text > nested text in props
  let content = text;
  if (!content && part?.text) {
    content = part.text;
  } else if (!content && typeof status === "object" && status !== null && "text" in status) {
    // Handle case where status is actually the part object (legacy)
    const s = status as Record<string, unknown>;
    if (typeof s.text === "string") content = s.text;
  }
  return <Markdown source={content ?? ""} />;
}
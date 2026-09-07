/**
 * Assistant text part (R4.2 step 2a).
 *
 * Wraps the assistant message's text content in the ``Markdown``
 * renderer. Used as the ``Text`` slot of ``MessagePrimitive.Parts``
 * for assistant messages so the thread renders markdown + GFM +
 * Shiki-highlighted code blocks (the wave-1 chat showed plain
 * pre-formatted text only).
 *
 * Streaming safety: the Markdown component is itself try/catch-
 * wrapped (see ``Markdown``). On a partial / unclosed markdown
 * fence mid-stream, it falls back to plain text rather than
 * throwing and tearing the thread tree down. The M1.8 invariant
 * is preserved.
 *
 * The component is typed as the exact ``TextMessagePartComponent``
 * shape from assistant-ui 0.15.18 so it fits the
 * ``MessagePrimitive.Parts`` ``components.Text`` slot. The runtime
 * only uses ``text``; the rest of the props (status, id, etc.) are
 * accepted and ignored.
 */
import type { TextMessagePartComponent } from "@assistant-ui/react";
import { Markdown } from "./Markdown";

export const AssistantTextPart: TextMessagePartComponent = (props) => {
  // The TextMessagePartProps shape gives us `text` + `status` + a
  // few other fields; we only render the text. The rest is
  // forwarded to no DOM element (the parent primitive is
  // responsible for the message wrapper).
  return <Markdown source={props.text} />;
};
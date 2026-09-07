# R4.2 — Visual polish spec (2026-09-07)

**Audience**: the planner amending `docs/R4_2_PLAN.md`. The current
plan under-scopes the visual polish (it frames it as a step-3 cosmetic
finish). The user reviewed the current `/chat` surface on 2026-09-07
and rejected it: the threads render, but the visual is unpolished and
"frankly bad." The bars named in the plan + `DESIGN.md §8` (assistant-ui
+ agent-elements, LibreChat-grade polish) are the contract; the work
to reach them was never specified as its own step.

This document:
1. Anchors the visual reference (LibreChat v0.8.x + assistant-ui shadcn
   registry).
2. Diagnoses the gap between R4.2 step 1/2a and the bar.
3. Specifies the surfaces to the bar (per-region).
4. Specifies the visual test (replace the markdown-only `/dev/chat-lab`
   with a **real-Thread** visual test).
5. Proposes a new step structure (a "polish" step before the current
   step 2b/2c/3) so the planner can amend.

## 1. Visual reference

| Reference | Why |
|---|---|
| [LibreChat v0.8.x — Message Actions](https://www.librechat.ai/docs/features/message_actions) | Primary visual/interaction reference. `DESIGN.md §4` and `docs/R4_PLAN.md` both name it. The 2026 surface (Right-aligned user turns, full-width assistant turns with avatar + header + footer, action bar on hover, copy/edit/rerun/fork/regenerate, code block actions) is the bar. |
| [LibreChat — Chat Interface (DeepWiki)](https://deepwiki.com/danny-avila/LibreChat/6.1-chat-interface) | Architectural reference: content parts (`text`, `tool_call`, `think`, `error`, `agent_update`), streaming cursor (`result-streaming` CSS class), memoized renders per content part, conversation branching, multi-modal composer. We mirror the part types; the streaming-cursor CSS class is the streaming indicator. |
| [LibreChat — Forking Chats](https://www.librechat.ai/docs/features/fork) | Conversation branching (fork from any message; visible-path copy; full-history copy). The plan already cites the R6 dispatch layering, but the UI surface (branch picker `n/m` stepper beside each message) is the assistant-ui shadcn-registry default. |
| [assistant-ui — Thread (shadcn registry)](https://www.assistant-ui.com/elements/thread) | The prebuilt shadcn-registry Thread component (`components/assistant-ui/elements/thread.aui.tsx`) — `autoScroll`, `turnAnchor`, `scrollToBottomOnRunStart`, `hideWhenRunning` + `autohide="not-last"` on the action bar, message components slot, AuiIf conditionals. We own the copied file (shadcn model). |
| [assistant-ui — Thread install](https://www.assistant-ui.com/docs/installation) | `npx shadcn@latest add @assistant-ui/thread` + the supporting primitives (markdown-text, reasoning, tool-fallback, tool-group, follow-up-suggestions, attachment, file, image, tooltip-icon-button, button, skeleton, dialog, tooltip, avatar, collapsible, textarea, input). Each is a copied file we adjust. |
| [shadcn/ui — Message + Bubble](https://ui.shadcn.com/docs/components/base/message) | Layout primitives: `Message` (avatar, header, footer, align, group), `Bubble` (variant, align, reactions). The Message owns the row; Bubble owns the surface — same split LibreChat uses. |
| LobeChat | **Inspiration only** (per `DESIGN.md §8` and the R4.1 planner ruling: Apache + commercial-derivative conditions; no code lifted). We do not import or vendor it. |

## 2. Diagnosis — what R4.2 step 1/2a shipped vs. what's missing

### Shipped (R4.2 step 1 + step 2a, commits `bc47e50` + `d57da01`)

- assistant-ui `useExternalStoreRuntime` custom adapter over our REST + WS
  contract (`sweave-web/src/lib/chat/runtime.ts` + `useSweaveChatRuntime.ts`,
  11 vitest pinning the state machine).
- Thread + Message + Composer built from assistant-ui primitives
  (`sweave-web/src/components/thread/Thread.tsx`).
- assistant-ui 0.15.18 pinned (React 19 peer ok; spike REJECTED the
  opencode adapter as it would bypass our backend funnel).
- `Chat.tsx` rewired to `AssistantRuntimeProvider` + `Thread`.
- Markdown rendering for assistant text via
  `react-markdown@10.1.0` + `remark-gfm@4.0.1` (streaming-safe try/catch,
  copy button on code blocks, GFM tables/lists/blockquote). 7 new vitest.
- `/dev/chat-lab` (dev-only, gated by `import.meta.env.DEV`) renders
  the markdown surface + a few bubbles as a gallery.

### Missing — the polish gap

The chat surface today (R4.2 step 1/2a) renders messages but is
"unpolished and frankly bad" in the user's words. The gap, per
region:

#### Thread
- **Auto-scroll** on new messages (assistant-ui's `autoScroll`,
  `scrollToBottomOnRunStart`, `scrollToBottomOnThreadSwitch`): NOT
  wired. The user has to scroll manually on each new message.
- **Scroll-to-bottom affordance** when the user has scrolled up
  during streaming: NOT wired.
- **History loading skeleton** (the gap between the user picking a
  session and the REST fetch resolving): NOT wired.
- **Running state** indicator (a "thinking" cursor / pulse on the
  streaming bubble): partial — we have `MessagePartPrimitive.Text`
  with a `running` status, but no visible cursor / placeholder.
- **Message grouping** (consecutive assistant messages without an
  avatar between them; consecutive user messages without the right
  gutter): NOT wired. Currently each message has its own
  alignment + bubble.

#### Message bubble (assistant)
- **Avatar** on the assistant's identity column: NOT present.
- **Header** (sender name / model label / endpoint icon): NOT
  present.
- **Footer** (timestamp + per-message action bar: copy, edit,
  rerun, read-aloud, fork, feedback, regenerate): NOT present. The
  only current affordance is the markdown copy button on code blocks.
- **Action bar on hover** (or last-message always-visible,
  `autohide="not-last"`): NOT present.
- **Markdown styling** is functional (GFM tables, lists, code, copy
  button) but bubble width (~92%) + thin borders look bare vs.
  LibreChat's full-width text body with a clear identity column.

#### Message bubble (user)
- **Right-aligned** bubble: ✅.
- **Avatar** / identity: NOT present.
- **Hover actions** (edit, copy, regenerate, fork, quote, feedback):
  NOT present.
- **Edit affordance** (LibreChat's `Cmd+S` save / `Cmd+Enter` rerun):
  NOT present.

#### Tool / thinking surfaces
- **Tool call cards** (Bash/Edit-diff/Search/Plan/Subagent/MCP/Thinking):
  NOT shipped. The `agent-elements` adoption per `DESIGN.md §8` is
  the design path; nothing has been lifted yet.
- **`ask_human` Question card** (M1.9 escalation surfaced inline in
  the thread): NOT shipped. The escalation exists end-to-end
  (`EscalationStore` + `/api/delegations/{id}/answer`); the in-flow
  card is the gap.
- **Reasoning / thinking trace** (`:::thinking` blocks or explicit
  `ContentTypes.THINK`): NOT shipped. assistant-ui shadcn
  registry's `Reasoning` primitive is the path.

#### Composer
- **Send affordance**: ✅ (up-arrow button).
- **Stop-during-run** (the run is running, the send becomes a stop
  button): NOT wired. The `useSweaveChatRuntime` hook sets
  `isSendDisabled` during a turn; the visual state doesn't
  change.
- **Attachments** (file/image/audio, `MCPAppFile`): NOT wired.
- **Slash commands** / suggested prompts: NOT wired.
- **Audio / dictation**: NOT wired.
- **Keyboard** (Enter to send, Shift+Enter newline, `Cmd+S` save on
  edit): NOT wired.
- **Model picker** in the composer (the user can pick the model
  per-turn): NOT wired (the model is set per-session in the M1.x
  routing layer; the composer doesn't expose it).
- **Context usage / cost** display: NOT wired.
- **Empty states** (welcome screen with suggested prompts, similar to
  LibreChat's): NOT present; the current empty state is a single
  muted line "Send a message to start the conversation."

#### Header (top bar, where the SessionPicker lives)
- **Session name + model badge** in the header: present
  (`Topbar.tsx` shows project + path + session + WS state).
- **Share / export / temporary chat** affordances: NOT present
  (LibreChat has a "share" button + "temporary chat" toggle; out of
  R4.2 scope per the plan's "no backend change" non-goal).
- **"Maximize chat space"** toggle (hides the sidebar for
  focus mode): NOT present.

#### Sidebar (R4.1 step 2 — functional but bare)
- ✅ Project switcher dropdown.
- ✅ Session tree with active highlight + inline create form.
- ✅ Funnel nav (Chat, Children) + Pane-shells nav (Memory, Agents,
  Settings).
- **Tool icons + colored endpoint dots** (LibreChat's sidebar
  uses per-endpoint colors + icons): NOT present.
- **Multi-conversation / recent chats** in the sidebar: NOT present
  (we have one session per project; LibreChat has a chat list).
- **"New chat" affordance** at the top of the sidebar: NOT present.
- **Pinned / favorites**: NOT present.

#### Theming
- ✅ CSS-variable theming + 5 presets + custom-color editor
  (R4.1 step 1, step 2a theme tokens).
- **Per-message role color** (e.g. subtle accent on assistant
  identity column, like LibreChat's avatar+accent): NOT present.

### Why the gap wasn't visible in the visual test

`/dev/chat-lab` (R4.2 step 2a) renders **the markdown component +
a few bubble shells** — it does NOT render the real `Thread` with
the real `useSweaveChatRuntime` adapter, the real `MessagePrimitive`
flow, the real composer, or the real scroll viewport. So the lab
couldn't surface the polish gap; the user had to visit `/chat`
themselves to see the actual chat surface. **The lab is a
component-level smoke, not a Thread-level visual test.** That is
the root cause of the test-passing-but-looks-bad failure mode.

## 3. The visual spec (per region)

Anchored on LibreChat v0.8.x and the assistant-ui shadcn registry.
The shadcn model: we copy the registry files (e.g. `thread.aui.tsx`,
`markdown-text.tsx`, `tool-fallback.tsx`, `message.tsx`,
`bubble.tsx`, `tooltip-icon-button.tsx`) and own the copies. Custom-
coloured / domain-specific edits happen in the copies, not in
`node_modules`.

### 3.1 Thread (root)

- **Copy the assistant-ui shadcn-registry `Thread`** into
  `sweave-web/src/components/assistant-ui/elements/thread.aui.tsx`
  (one file; the shadcn-registry source). Wrap it in our route:
  `<Thread components={{ AssistantMessage, UserMessage, ToolFallback,
  Welcome }} />`.
- **Auto-scroll** defaults: `autoScroll={true}`,
  `scrollToBottomOnRunStart={true}`,
  `scrollToBottomOnThreadSwitch={true}`,
  `scrollToBottomOnInitialize={true}`.
- **`turnAnchor="bottom"`** (we anchor the user's new message at
  the bottom, not the top — `turnAnchor="top"` is the ChatGPT-style
  alternative; we pick the more familiar LibreChat pattern).
- **Empty state** (`<ThreadPrimitive.Empty>` or the `Welcome`
  component slot): the M1.x welcome screen. Show 3-4 suggested
  prompts sourced from the active project's recent task
  history (a small fetch, no backend change). Fallback to static
  prompts if the project has no history.
- **History loading skeleton** (`<ThreadPrimitive.If loading>` or
  similar): a subtle 3-bar shimmer while the REST fetch resolves.
- **Running indicator** on the streaming assistant bubble: the
  assistant-ui `result-streaming` CSS class (or equivalent) on the
  last message while `s.thread.isRunning`. The cursor is a pulsing
  dot at the end of the streamed text — minimal but visible.
- **Per-message spacing**: 24px between message rows, 12px between
  grouped messages from the same role (consecutive user → 12px;
  consecutive assistant → 12px). LibreChat uses `MessageGroup` for
  this; we use the shadcn `MessageGroup` slot.

### 3.2 Message bubble

- **User bubble**: right-aligned, max-width 75%, `bg-primary/10`,
  `rounded-2xl`, no header/footer for the user side (matches
  LibreChat). Hover actions on the bubble: copy, edit, regenerate
  (the user message can be edited and the conversation re-runs from
  the edit; the `EditComposer` is shadcn-registry's edit surface).
- **Assistant bubble**: full-width text body (LibreChat's pattern
  is "assistant keeps a full-width response layout"). Avatar +
  header (model label + endpoint icon + sender name) + footer
  (timestamp + action bar). Action bar appears on hover OR always
  on the last message (`hideWhenRunning` + `autohide="not-last"`
  per the shadcn-registry Thread).
- **Action bar** (assistant-ui `ActionBarPrimitive`):
  - **Copy** (full message text; per LibreChat, joins only
    user-visible text parts in order; reasoning + tool calls are
    omitted).
  - **Edit** (in-place editor; `Cmd/Ctrl+S` to save, `Cmd/Ctrl+Enter`
    to save-and-rerun, `Escape` to cancel).
  - **Regenerate** (re-run the parent user turn; the new response
    is a sibling of the current one; the branch picker surfaces
    it). The current run is cancelled first.
  - **Fork** (branch from this message into a new conversation;
    out of R4.2 scope per the plan's "no backend change" non-goal;
    we surface the affordance in the menu but disable with a
    "coming in R4.3" tooltip — LibreChat's affordance is the
    reference, the backend lands with R4.3's delegation tree).
  - **Read aloud** (TTS via `SpeechSynthesis`; out of R4.2 scope;
    surfaced as a disabled affordance).
  - **Feedback** (thumbs up/down; M1.x has no `feedback` table;
    out of R4.2 scope; surfaced as a disabled affordance that
    fires a notification).
  - **Branch picker** (`BranchPickerPrimitive`): the `n/m`
    stepper beside messages with siblings (LibreChat / ChatGPT).

### 3.3 Tool call + thinking surfaces

- **Tool call group** (`<ToolGroup>` / `<ToolFallback>` from
  assistant-ui shadcn registry): renders a run of consecutive tool
  calls grouped together. Each tool call has a per-tool UI
  (Bash/Edit-diff/Search/Plan/Subagent/MCP/Thinking) — **the
  `agent-elements` adoption is the path** (per `DESIGN.md §8` +
  the R4.2 plan step 2c). For step 2 (polish), we ship the
  `ToolFallback` slot with a generic card (icon + tool name +
  collapsed args + expanded output + status pill) and the
  per-tool cards as the step-2c follow-up.
- **Reasoning / thinking trace** (`<Reasoning>` from assistant-ui
  shadcn registry): a collapsible "Thought for Xs" header that
  expands the chain-of-thought content. The reasoning content
  comes from the M1.9 parts-model trace via
  `GET /api/delegations/{id}/detail` (the `ReasoningMessagePart`
  is in the `tool_timeline[]` when the orchestrator emits a
  reasoning block).
- **Escalation / Question card** (`<Question>` or equivalent
  lifted from `agent-elements`): for `ask_human` escalations
  surfaced inline in the thread. Renders the question text +
  options (if any) + an input + a Submit button. POST to
  `/api/delegations/{id}/answer`. Step-2c scope; the polish step
  ships the layout slot (empty card on the assistant bubble) so
  the step-2c card plugs in cleanly.

### 3.4 Composer

- **Copy the assistant-ui shadcn-registry Composer** (or build
  from `ComposerPrimitive.Root/Input/Send/Cancel`) with our
  `useSweaveChatRuntime.onNew` integration.
- **Send affordance**: up-arrow button (current). When the run is
  in flight, the button morphs to a **stop** square (the
  `ComposerPrimitive.Send asChild` pattern, swapping the icon +
  the click handler to call `onCancel`).
- **Stop affordance**: `onCancel={() => /* signal the backend
    to cancel via /api/delegations/{id}/cancel */}` — out of
    R4.2 scope per "no backend change" (the cancel endpoint is
    M1.4 territory; we surface the affordance with a notification
    that it's not yet wired).
- **Keyboard** (R4.2 step 3 polish, but ship the Enter/Shift+Enter
  binding in the polish step): Enter sends, Shift+Enter inserts
  newline. `useTextarea`-style hook reads the `ComposerPrimitive.Input`
  ref. Configurable via the `enterToSend` toggle in Settings
  (R4.4).
- **Attachments** (R4.4 — out of R4.2 scope per plan): the
  `ComposerAttachmentAdapter` slot.
- **Model picker** in the composer: a small dropdown to the left
  of the input, showing the per-session model (and the per-role
  alternatives from the routing layer). Out of R4.2 scope per
  the plan's "no backend change" non-goal; the affordance can
  surface in the polish step as a read-only badge (step 2.5
  spec below).
- **Empty state** (welcome): the Thread's `<Welcome>` component
  slot, rendered when the thread is empty.

### 3.5 Sidebar (R4.1 step 2 — keep + extend)

- The existing `ProjectSwitcher` + `SessionTree` + nav stay
  functional; the polish step extends with:
  - Per-endpoint icons + colored dots (sourced from
    `/api/harnesses`).
  - "New chat" button at the top (creates a new session in the
    active project; the existing inline form covers it).
  - Hover actions on each session (rename, delete, export).
  - "Pinned" / "favorites" — out of R4.2 scope; surfaced as a
    disabled affordance.
  - The collapse toggle (current) stays; the polish step makes
    the collapsed state a single-icon rail (LibreChat's pattern)
    instead of a 64px slab.

### 3.6 Topbar (current)

- ✅ Project + path + session + WS state.
- Polish step: replace the `Topbar.tsx` connection-state pill with
  a per-endpoint icon, add the model + cost display (read-only
  from `/api/delegations?session_id=...&limit=1`).

## 4. Visual test approach (real-Thread lab)

**Replace the markdown-only `/dev/chat-lab` with a real-Thread
visual test.** The current lab is a component gallery; the polish
step's lab is the **actual `Thread` component** with a seeded
runtime. That is the only surface that would have surfaced the
polish gap.

### 4.1 Route + gating

- Replace the markdown lab with `/dev/chat-lab` (same path; the
  old lab is removed) that renders the **real `Thread` component**
  + the **real `Sidebar` + `Topbar`** inside a mock `AppProvider`
  (an `AppProviderStub` that provides a fake active project +
  session + dispatch shims, so the real `useSweaveChatRuntime` can
  be called with a real `sessionId`).
- The Thread's runtime is the **real `useSweaveChatRuntime`
  adapter** wired to a fake event bus that:
  1. On submit, replays a fixture event sequence (5-message
     thread with markdown + code block + tool card + pending
     Question).
  2. Supports a "replay" button that re-runs the sequence with a
     different assistant response (so the user can see branching
     + regeneration).
  3. Supports a "streaming" button that fires `chat.delta` events
     one chunk at a time (so the user can see the streaming
     cursor in real time).

### 4.2 Sections (the gallery)

Each section is a different state of the real Thread:
1. **Empty thread** (welcome screen + suggested prompts).
2. **Mid-stream** (the last assistant bubble has `status: running`
   + a visible cursor; the toolbar shows the stop button instead
   of send).
3. **Resolved turn** (5-message thread with markdown + a code
   block + a tool card + a pending Question card).
4. **Action bar hover** (the last message's action bar shown
   explicitly via `autohide="never"` for the gallery).
5. **Composer states** (idle / streaming-with-stop / disabled
   while submitting).
6. **Markdown surface** (the same markdown demo from R4.2 step 2a,
   but rendered inside the real `Thread` via the
   `MessagePrimitive.Parts components={{ Text: AssistantTextPart }}`
   slot, not as a standalone gallery).
7. **Tool timeline** (a tool card + a reasoning trace, rendered
   inside the thread).
8. **Escalation / Question card** (the M1.9 `ask_human` flow).

### 4.3 Vitest surface (the CI gate)

- 7 vitest from R4.2 step 2a (markdown + chat-lab sections) stay.
- 5 new vitest (real-Thread lab) pin:
  - **Empty state** renders the welcome screen.
  - **Mid-stream** state renders the stop button.
  - **Action bar** hover rendering (autohide="never" branch).
  - **Composer disabled** during a streaming turn.
  - **Suggested prompts** render the configured prompts.

## 5. Proposed step structure (replaces the current step 2b/2c/3)

The current `docs/R4_2_PLAN.md` has step 2 split as:
- **2a** (done): markdown + GFM + copy button + dev-only lab.
- **2b** (~0.4, planned): shiki deferred highlight + tool cards
  lifted from agent-elements + Question card.
- **2c** (~0.3, planned): tool timeline in-thread + Question card
  in-thread.
- **3** (~0.3, planned): session lifecycle in-thread + polish.

**The polish is not step 3.** It is the bulk of the visual work
and must happen before 2b/2c (the cards live in the polished
message bubble, not as a free-floating gallery). The proposed
amendment:

### 5.1 Insert a new step "R4.2 step 2-pre" (or "2.5"): visual polish (~0.6)

- Copy the assistant-ui shadcn-registry Thread + supporting
  primitives (`thread.aui.tsx`, `markdown-text.tsx`,
  `tooltip-icon-button.tsx`, `button`, `skeleton`, `dialog`,
  `tooltip`, `avatar`, `textarea`, `input`).
- Build the message bubble (avatar + header + footer + action
  bar) per §3.2.
- Build the composer stop affordance per §3.4.
- Wire auto-scroll per §3.1.
- Build the **real-Thread visual test** per §4.
- Docs: `DESIGN.md §4` (chat surface row → polished ✅), R4 hub
  row flipped, `R4_2_PLAN.md` step-2b/2c updated to depend on
  this step's primitives.
- **Gates**: vitest (~84) including the 5 new real-Thread
  vitest; `npm run build` green; pytest 458/458 untouched (the
  polish is UI-only).

### 5.2 After 2-pre: the original 2b/2c scope, now cheaper

- 2b (shiki + tool cards + Question card) plugs into the
  polished message bubble / composer / scroll viewport
  primitives from 2-pre. The bubble + footer + action bar are
  already styled.
- 2c (tool timeline + Question card in-thread) is now "wire
  the card into the existing bubble layout," not "build the
  bubble + card together."

### 5.3 Step 3 stays as planned (lifecycle + polish finishing)

- The "polish finishing" framing in step 3 is then correct: small
  refinements (entrance animations, prefers-reduced-motion,
  long-pasted-text handling, mobile responsive) rather than
  building the visual design from scratch.

## 6. Reference to this document in the plan

When the planner amends `docs/R4_2_PLAN.md`, add a single line
under the existing "Amendment (2026-09-06...)" block:

> **Amendment (2026-09-07, user ruling — mid-execution)**: the
> R4.2 visual polish is the bulk of step 2, not a step-3
> cosmetic finish. The user rejected the step-1/step-2a chat
> surface as "unpolished and frankly bad." New step 2-pre (~0.6):
> copy the assistant-ui shadcn-registry Thread + supporting
> primitives, build the polished message bubble (avatar + header
> + footer + action bar), wire the composer stop affordance, build
> the auto-scroll viewport, replace `/dev/chat-lab` with a
> real-Thread visual test. Full design spec:
> `docs/R4_2_VISUAL_POLISH.md` (the planner-facing artifact for
> this ruling). Steps 2b/2c/3 follow as the original plan
> framed, now built on top of the polished primitives.

## 7. Risks + call-outs

- **Bundle size**: copying the assistant-ui shadcn registry adds
  ~150-200kB to the bundle (markdown-text, tool-fallback, message,
  bubble, avatar, tooltip, etc.). The Chat route should be
  code-split (`React.lazy` for the Thread component) in the
  polish step. Step 4's code-split task becomes "Chat route
  already split; tighten the markdown sub-bundle."
- **React 19 + assistant-ui 0.15.18 shadcn-registry copies**:
  the registry source is the source of truth. When the package
  releases a 0.16.x with breaking primitives changes, we re-copy
  the affected files and re-apply our edits. The shadcn model
  expects this.
- **`lucide-react` icon set** is already at 1.41.0 (R4.1 step 1c).
  The polish step's icon set is small (`User`, `Bot`, `Copy`,
  `Check`, `RefreshCw`, `Pencil`, `Share`, `Volume2`, `ThumbsUp`,
  `ThumbsDown`, `StopCircle`, `ArrowUp`, `Loader2`,
  `Sparkles`).
- **Cursor streaming animation**: CSS-only (no JS animation loop).
  Honors `prefers-reduced-motion`. Three-line CSS at most.
- **Tool card data path** (R4.2 step 2c): the M1.9 parts-model
  trace is fetched via `GET /api/delegations/{id}/detail` after
  `message.added(assistant)` (using the assistant message's
  `metadata.delegation_id`). The polish step reserves the bubble
  layout slot (footer / action bar); the data fetch + per-tool UI
  is step 2c. **No backend change** required.
- **Branch picker + regenerate + edit** in the action bar need
  backend support (the R4.3 delegation tree). The polish step
  ships the affordances as **disabled with a tooltip** until R4.3
  lands; the icons + UX are in place from day one.

## 8. Open questions for the planner

1. **Does step 2-pre fit in this round, or is it a separate round?**
   The plan budget is "~0.75 sessions" for R4.2 step 2; the polish
   alone is ~0.6. The user explicitly asked for the chat surface
   to be LibreChat-grade before any more code. The answer
   determines whether R4.2 is a single round (2-pre + 2b + 2c
   folded) or a multi-round (2-pre first, then 2b/2c in the
   next round).
2. **Cancel during run** (R4.2 step 2c's "R4.2 step 3 polish"
   doesn't include it; the plan's "no backend change" non-goal
   doesn't either). Should the polish step surface a stop
   button that hits the existing chat-loop `asyncio.Lock` cancel
   path (which IS in the backend) or a notification that the
   affordance is pending?
3. **Edit message + rerun** (R4.3 territory). The polish step
   ships the affordance disabled; is that acceptable for R4.2
   review, or do we want to defer the affordance to R4.3 entirely
   and ship a smaller action bar in the polish step?
4. **`/dev/chat-lab` removal**: the markdown-only lab is replaced
   by the real-Thread lab. The markdown demo (step 2a) survives
   as section 6 of the new lab (rendered inside the real Thread).
   Confirm the markdown demo's vitest survive as the
   `Markdown.test.tsx` file (they pin the renderer directly, not
   the Thread).

/**
 * Domain types (M1.9 Step 1).
 *
 * The previous version of this file held v1-era ``any``-typed
 * shapes. This rewrite uses the M1.9 v2 surface (delegations,
 * specialists, sessions, escalations, detail) with strict types
 * throughout. Unknown fields are still tolerated on the wire
 * (the API client returns narrow shapes; the unknown fields
 * simply aren't surfaced).
 *
 * The shapes mirror ``sweave/api/projects.py``,
 * ``sweave/web/routers/delegations.py``, etc. The Pydantic
 * models on the server are the source of truth; this file
 * mirrors them with hand-rolled interfaces (no codegen; the
 * surface is small and stable enough to hand-maintain).
 */

// ---------- Projects ----------

export interface ProjectSummary {
  name: string;
  path: string;
  description: string;
  created_at: string;
  updated_at: string;
  default_harness: string;
  memory_bank: string;
  model_overrides: Record<string, string>;
  routing_rules: RoutingRule[];
  /** M1.9 step 2: per-project worktree_base override. */
  worktree_base: string | null;
  active: boolean;
}

export interface ProjectCreate {
  name: string;
  path: string;
  description?: string;
  worktree_base?: string | null;
}

export interface RoutingRule {
  pattern: string;
  agent: string;
  model?: string;
}

// ---------- Sessions ----------

export interface SessionSummary {
  id: string;
  name: string;
  project_name: string;
  created_at: string;
  updated_at: string;
  /** M1.7 step 1: per-Session orchestrator session id. */
  orchestrator_session_id: string | null;
  status: "active" | "paused" | "completed";
  message_count: number;
  child_count: number;
  memory_bank: string;
  active: boolean;
}

export interface SessionMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  agent: string | null;
  tool_name: string | null;
  tool_result: string | null;
  metadata: Record<string, unknown>;
}

export interface SessionChild {
  id: string;
  parent_session_id: string;
  agent_name: string;
  task: string;
  worktree_path: string | null;
  status: "running" | "completed" | "failed";
  created_at: string;
  completed_at: string | null;
  output: string;
  error: string | null;
  /** M1.1: link to the runtime Delegation record. */
  delegation_id: string | null;
}

export interface SessionDetail extends SessionSummary {
  messages: SessionMessage[];
  children: SessionChild[];
}

/**
 * Active-turn snapshot (2026-09-10 recovery contract). From
 * `GET /api/sessions/{id}/turn` (``turn`` field when ``active``) or
 * the HTTP 409 body of a second POST while a turn is running
 * (``detail.turn``). In-memory only: never durable across a server
 * restart (``active: false`` then, always).
 */
export interface TurnSnapshot {
  session_id: string;
  delegation_id: string | null;
  status: string;
  /** waiting | streaming | question */
  phase: string;
  started_at: string;
  stream_text: string;
  thinking_text: string;
  pending_question: boolean;
  /** Multi-message turns (2026-09-11): the live round (0 = first
      turn, 1 = synthesis). Absent on old servers — readers default
      to 0. */
  round?: number | null;
  /** Tool transparency: the live round's compact tool rows (latest
      status wins per callID). Absent on old servers — readers
      default to []. */
  tools?: ChatToolRow[] | null;
}

/**
 * Compact tool-call row (chat transparency, opencode-style).
 * From the ``chat.tool`` WS event (live) or the assistant message's
 * ``metadata.tools[]`` (persisted). UI-only: the composer never
 * reads it, so it cannot leak into the model context.
 */
/**
 * Per-tool additive `detail` blob (TOOL_CARDS plan step 2, 2026-09-16).
 *
 * One builder (`sweave/chat/tools.py:build_tool_detail`) serves BOTH the
 * chat rows (`ChatToolRow.detail`) and the detail surface
 * (`ToolTimelineEntry.detail`), so the two renderers never diverge.
 * Every field is OPTIONAL: legacy servers (pre step-1) emit NO `detail`
 * key at all, and the chat card degrades to today's one-liner. The bash
 * `output_excerpt` rides its own optional field so a 32K result stays
 * 2K in the thread; the full text already lands in the trace / detail
 * surface. Never a crash, never a leak (composer isolation holds).
 */
export interface ToolRowDetail {
  // read: structured window (F3 — render from this, never the footer).
  window?: {
    shownFrom?: number | null;
    shownTo?: number | null;
    total?: number | null;
    nextOffset?: number | null;
    from_footer?: boolean | null;
  } | null;
  // write: create-vs-overwrite + churn stats (F4).
  mode?: "create" | "overwrite" | null;
  linesAdded?: number | null;
  linesRemoved?: number | null;
  preview?: string | null;
  old_capture?: string | null;
  // edit: churn stats (old/new already in `input`).
  // bash: command ALWAYS visible (F2) + structured exit + 2K excerpt.
  command?: string | null;
  exit?: number | null;
  output_excerpt?: string | null;
  truncated?: boolean | null;
  // grep: pattern (+ path/include, match count) — NEVER match content.
  pattern?: string | null;
  path?: string | null;
  include?: string | null;
  matchCount?: number | null;
  // glob: pattern + count.
  count?: number | null;
  // git: verb + args.
  verb?: string | null;
  args?: unknown;
  // todo: titles list.
  titles?: string[] | null;
  // Fail-safe: the detail blob was clipped to DETAIL_JSON_CHARS.
  clipped?: boolean | null;
}

export interface ChatToolRow {
  callID: string;
  tool: string;
  /** pending | running | completed | error | unknown */
  status: string;
  /** One-liner detail (path / command / pattern). */
  summary: string;
  title?: string | null;
  /** Capped input JSON (edit/write rows only — the expandable diff). */
  input?: Record<string, unknown> | null;
  round?: number | null;
  /**
   * TOOL_CARDS step 2: additive per-tool detail (see {@link ToolRowDetail}).
   * Absent on legacy servers — readers MUST degrade to the one-liner.
   */
  detail?: ToolRowDetail | null;
  /**
   * TOOL_CARDS step 2: the 2K inline bash tail (F2). Absent on legacy
   * servers and on non-bash rows. `detail.output_excerpt` is the
   * capped source-of-truth; this mirrors it on the row for cheap
   * access. Readers fall back to `detail.output_excerpt` when absent.
   */
  output_excerpt?: string | null;
}

/**
 * One ordered timeline item (chat transparency, opencode-style).
 * Text/reasoning carry their slice; tool markers carry the callID
 * (the row lookup is the message's `metadata.tools[]` / the live
 * entry's tools). The renderer joins contiguous same-kind runs.
 */
export type ChatSegment =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool"; callID: string };

export interface SessionCreate {
  name: string;
  project_name?: string;
}

// ---------- Specialists ----------

export interface SpecialistSummary {
  name: string;
  scope: "project" | "global" | "seed";
  is_orchestrator: boolean;
  role_ref: string | null;
  description: string;
  system_prompt: string;
  harness: string;
  /** Worktree isolation policy (per-specialist user toggle, never an
      LLM parameter): isolated | inherit | absent on legacy payloads. */
  worktree_policy?: string;
  current_model: string | null;
  session_id: string | null;
}

export interface SpecialistCreate {
  name: string;
  description?: string;
  system_prompt?: string;
  harness?: string;
  role_ref?: string;
  current_model?: string;
  worktree_policy?: string;
}

// ---------- Delegations + escalations + detail ----------

export type DelegationStatus =
  | "queued"
  | "running"
  | "review"
  | "done"
  | "failed";

export interface Delegation {
  schema_version: number;
  delegation_id: string;
  task_id: string;
  agent: string;
  model: string;
  task: string;
  status: DelegationStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  parent_session_id: string | null;
  project_name: string | null;
  output: string;
  error: string | null;
  worktree_path: string | null;
  branch: string | null;
  pr_url: string | null;
  parent_task_id: string | null;
  manifest: Record<string, unknown> | null;
  depth: number;
  chain_root_id: string | null;
  coordination_tokens: number;
  kind: "task" | "chat";
  needs_attention: boolean;
  /** M1.13 step 4 ARCHIVE-not-delete (ruling 2026-09-11); backend exposes the flag on every row. */
  archived?: boolean;
  archived_at?: string | null;
}

/**
 * M1.13 step 5: compact per-project archive aggregate
 * (``GET /api/delegations?include_archived=true`` ->
 * ``archived_projects[]``; shape mirrors sweave/runtime/
 * delegation_archive.py ``archive_group_entry``). No token sum
 * is exposed; live-store rows read ``source: "store"``, the
 * persisted ``~/.sweave/archived`` index rows ``"index"``.
 */
export interface ArchivedProjectSummary {
  project_name: string;
  workdir: string | null;
  total: number;
  by_status: Record<string, number>;
  by_kind: Record<string, number>;
  archived_at: string | null;
  last_created_at: string | null;
  source: "store" | "index";
}

/**
 * Transcript block — rescoped Step 2a (engine-first read path).
 *
 * One block per specialist turn (chronological conversation order),
 * projected from the engine sidecar journal into an additive detail
 * key. The backend thread locks the wire key name; this slice binds
 * to `transcript` (the plan default) and degrades gracefully when
 * the key is absent or empty (opencode turns that have not hardened
 * their capture yet). Never a crash, never an empty promise: a
 * present-but-empty transcript renders the frozen-state notice, not
 * a blank tab.
 */
export interface TranscriptBlock {
  /** Stable id for the block (turn id / user_message_id). */
  id: string;
  role: "prompt" | "assistant" | "system" | "tool";
  /** The prompt actually sent (system render + preamble + task). */
  prompt?: string | null;
  /** Assistant text slice. */
  text?: string | null;
  /** Reasoning slice (when captured). */
  reasoning?: string | null;
  /** Tool calls with lifecycle (callID-keyed, snapshot state). */
  tools?: ToolTimelineEntry[];
  /** Per-block token usage (counts only). */
  tokens?: {
    input?: number;
    output?: number;
    reasoning?: number;
    cache_read?: number;
    cache_write?: number;
  } | null;
}
export interface DelegationDetail {
  delegation_id: string;
  composed_prompt: ComposedPrompt | null;
  tool_timeline: ToolTimelineEntry[];
  tokens: Tokens | null;
  status_timeline: StatusChange[];
  /** Additive read path (rescoped 2a). Absent until the backend
   *  thread lands; readers treat undefined/empty as "no transcript
   *  projected yet" and degrade to the frozen-state copy. */
  transcript?: TranscriptBlock[] | null;
}

export interface ComposedPrompt {
  memory_chars: number;
  whats_new_chars: number;
  synthesis_chars: number;
  transcript_ref_chars: number;
  user_chars: number;
  dropped_memory: number;
  dropped_whats_new: number;
  dropped_synthesis: number;
}

export interface ToolTimelineEntry {
  callID: string;
  tool: string | null;
  status: string | null;
  output?: unknown;
  error?: string | null;
  title?: string | null;
  input?: unknown;
  time?: { start?: number; end?: number } | null;
  states: Array<Record<string, unknown>>;
  started_at: string | null;
  /**
   * TOOL_CARDS step 2: additive per-tool detail (see {@link ToolRowDetail}).
   * The detail-view builder projects it from the latest tool state so the
   * Tools tab + Transcript tab render the enriched window/counts/stats.
   * Absent on legacy traces — readers degrade to the raw output card.
   */
  detail?: ToolRowDetail | null;
}

export interface Tokens {
  input: number;
  output: number;
  reasoning: number;
  cache_read: number;
  cache_write: number;
  cost: number;
  /** Peak live context (max single-step prompt). Optional for
   * pre-split payloads; readers must `?? 0`. */
  context_input?: number;
}

/**
 * Usage-ledger cell (counts and shapes only — never text).
 * From `GET /api/stats/summary` (`sweave/stats/ledger.py`).
 * Phase 1b cost fields: `estimated_cost` (summed per turn via the
 * shared `sweave/stats/pricing.py` fold), `cost_source` (worst turn
 * source: none > rates > provider), `unpriced` (no turn priced —
 * display "unpriced", never $0). Optional: pre-1b servers omit them;
 * readers must default (missing cost reads as unpriced, never crash).
 */
export interface StatsCell extends Tokens {
  turns: number;
  failed: number;
  estimated_cost?: number;
  cost_source?: "provider" | "rates" | "none";
  unpriced?: boolean;
}

export interface StatsSummary {
  window_days: number;
  generated_at: string;
  totals: StatsCell & { wall_seconds: number; completed_turns: number };
  by_day: Array<{ day: string } & StatsCell>;
  by_model: Array<{ model: string } & StatsCell>;
  by_project: Array<{ project: string } & StatsCell>;
  by_agent: Array<{ agent: string } & StatsCell>;
  by_kind: Array<{ kind: string } & StatsCell>;
  by_status: Record<string, number>;
  by_error: Array<{ error: string; count: number }>;
}

export interface StatusChange {
  status: DelegationStatus;
  source: string | null;
  ts: string;
}

/**
 * One additive question in a batched (TOOL_CARDS Step 3) escalation.
 * The batch holds a single `questions[]` (≤5) on one record; a
 * legacy single-question record has NO `questions[]` and instead
 * uses the top-level `question` / `options` fields. Readers MUST
 * degrade: absent `questions` → render as the today's single card
 * via the top-level fields.
 */
export interface EscalationQuestion {
  question: string;
  /** Optional single-pick options (render as buttons). */
  options?: string[] | null;
}

export interface EscalationRecord {
  escalation_id: string;
  delegation_id: string;
  question: string;
  options: string[] | null;
  kind: "question" | "escalation" | "permission";
  audience: "human" | "orchestrator";
  status: "pending" | "answered" | "skipped" | "timeout";
  created_at: string;
  deadline_at: string | null;
  answered_at: string | null;
  response: string | null;
  /** M1.12: structured detail for permission questions (requestID,
   *  patterns, command). */
  metadata?: Record<string, unknown> | null;
  /**
   * TOOL_CARDS Step 3 (additive): batched questions on ONE record
   * (the ask_human `questions[]` payload, ≤5). Absent on legacy
   * single-question records — those degrade to the top-level
   * `question` / `options` fields. Render per-question state from
   * the answers array below; never assume all-or-nothing.
   */
  questions?: EscalationQuestion[] | null;
  /**
   * TOOL_CARDS Step 3 (additive): parallel to `questions[]`. Index
   * `i` holds the answer to `questions[i]` (or `null` when still
   * pending). Absent / shorter-than-questions on legacy payloads —
   * readers extend with `null`s. Partials persist WITHOUT resolving
   * the record until every slot is filled (all-at-once flip).
   */
  answers?: (string | null)[] | null;
}

// ---------- Models + rules + config ----------

export interface ModelConfig {
  default: string;
  aliases: string[];
  provider: string;
}

export interface ModelsConfig {
  providers: Record<string, string[]>;
  all_models?: string[];
  /** Global default (orchestrator + specialists without a model). */
  default?: string | null;
  /** Reasoning-effort variants per qualified model id (effort dropdown). */
  variants?: Record<string, string[]>;
}

export interface HarnessInfo {
  name: string;
  display_name: string;
  command: string;
  version: string;
  providers: string[];
  models: string[];
}

/**
 * Part + contract version matrix (per-part versioning ruling
 * 2026-09-20). From `GET /api/version`: parts identify
 * (backend/engine/web), contracts gate compatibility
 * (engine_protocol, delegation_schema). Null = unreadable server-side.
 */
export interface VersionMatrix {
  backend: string | null;
  engine: string | null;
  web: string | null;
  engine_protocol: string;
  delegation_schema: number;
}

/**
 * Provider credential availability (2026-09-14 credential-ownership:
 * `~/.sweave/credentials.json` canonical). From `GET /api/providers`:
 * the absolute catalog (universe) annotated with availability.
 * Secrets never leave the server — only suffix + tier source.
 */
export interface ProviderAvailability {
  id: string;
  models: string[];
  /** true = credential in some tier, false = none, null = local/keyless. */
  connected: boolean | null;
  /** Credential tier: env | sweave | opencode-legacy (null when unconnected). */
  via: string | null;
  key_suffix: string | null;
  local: boolean;
}

export interface PendingImport {
  provider: string;
  source: string;
  /** Why it is pending ("new in opencode" | "rotated in opencode" | oauth…). */
  reason?: string;
  /** Opencode-store paths that disagree (isolated copy + real store). */
  sources?: string[];
}

export interface Worktree {
  path: string;
  branch: string;
  task_id: string;
  agent: string;
  created_at: string;
  pr_url: string | null;
  pr_number: number | null;
}

// ---------- WS envelope ----------

export interface WSEnvelope {
  event: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export type WSHandler = (envelope: WSEnvelope) => void;

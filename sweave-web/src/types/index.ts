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
}

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

export interface DelegationDetail {
  delegation_id: string;
  composed_prompt: ComposedPrompt | null;
  tool_timeline: ToolTimelineEntry[];
  tokens: Tokens | null;
  status_timeline: StatusChange[];
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
}

export interface Tokens {
  input: number;
  output: number;
  reasoning: number;
  cache_read: number;
  cache_write: number;
  cost: number;
}

export interface StatusChange {
  status: DelegationStatus;
  source: string | null;
  ts: string;
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

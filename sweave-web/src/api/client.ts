/**
 * Typed v2 API client (M1.9 Step 1).
 *
 * The v1-era client used ``any`` for almost every return type. This
 * rewrite pins strict types for the M1.9 surface: delegations,
 * specialists, sessions, escalations, the detail endpoint, the
 * M1.9 escalation/answer surface, and the chat endpoint. The
 * response shapes mirror the server Pydantic models; unknown
 * fields are tolerated on the wire (the axios response is
 * narrowed via the typed method signatures).
 *
 * Auth: the v2 surface (delegations, sessions, specialists) does
 * NOT require auth -- the MCP-only endpoints (`/api/mcp/...`) do
 * (the ``X-Sweave-MCP-Token`` header), but the React UI doesn't
 * call those. Auth headers can be wired in later when a
 * multi-user mode lands.
 */
import axios, { AxiosInstance } from "axios";
import type {
  ArchivedProjectSummary,
  Delegation,
  DelegationDetail,
  EscalationRecord,
  HarnessInfo,
  ModelsConfig,
  PendingImport,
  ProjectCreate,
  ProjectSummary,
  ProviderAvailability,
  SessionCreate,
  SessionDetail,
  SessionMessage,
  SessionSummary,
  SpecialistCreate,
  SpecialistSummary,
  StatsSummary,
  TurnSnapshot,
  Worktree,
} from "@/types";

const API_BASE_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

class ApiClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      headers: { "Content-Type": "application/json" },
      timeout: 30_000,
    });
    this.client.interceptors.response.use(
      (r) => r,
      (err) => {
        if (err.response?.status === 401) {
          // The v2 surface is localhost-only; 401 = an MCP endpoint
          // was called without the right header. Log + rethrow.
          // eslint-disable-next-line no-console
          console.error("401 from API:", err.config?.url);
        }
        return Promise.reject(err);
      },
    );
  }

  // ---- Projects ----

  async listProjects(): Promise<ProjectSummary[]> {
    const r = await this.client.get<{ projects: ProjectSummary[] }>(
      "/projects",
    );
    return r.data.projects;
  }

  async getActiveProject(): Promise<ProjectSummary | null> {
    const r = await this.client.get<ProjectSummary | null>("/projects/active");
    return r.data;
  }

  async createProject(body: ProjectCreate): Promise<{ success: boolean; project: ProjectSummary }> {
    const r = await this.client.post<{ success: boolean; project: ProjectSummary }>(
      "/projects",
      body,
    );
    return r.data;
  }

  async setActiveProject(name: string): Promise<{ success: boolean; active_project: string }> {
    const r = await this.client.post<{ success: boolean; active_project: string }>(
      `/projects/${encodeURIComponent(name)}/active`,
      {},
    );
    return r.data;
  }

  async deleteProject(name: string): Promise<{ success: boolean }> {
    const r = await this.client.delete<{ success: boolean }>(
      `/projects/${encodeURIComponent(name)}`,
    );
    return r.data;
  }

  // ---- Sessions ----

  async listSessions(projectName?: string): Promise<SessionSummary[]> {
    const r = await this.client.get<{ sessions: SessionSummary[] }>(
      "/sessions",
      { params: projectName ? { project_name: projectName } : {} },
    );
    return r.data.sessions;
  }

  async getActiveSession(): Promise<SessionSummary | null> {
    const r = await this.client.get<SessionSummary | null>("/sessions/active");
    return r.data;
  }

  async getSession(sessionId: string): Promise<SessionDetail | null> {
    const r = await this.client.get<SessionDetail>(
      `/sessions/${encodeURIComponent(sessionId)}`,
    );
    return r.data;
  }

  async createSession(body: SessionCreate): Promise<{ success: boolean; session: SessionSummary }> {
    const r = await this.client.post<{ success: boolean; session: SessionSummary }>(
      "/sessions",
      body,
    );
    return r.data;
  }

  async setActiveSession(sessionId: string): Promise<{ success: boolean; active_session: string }> {
    const r = await this.client.post<{ success: boolean; active_session: string }>(
      `/sessions/${encodeURIComponent(sessionId)}/active`,
      {},
    );
    return r.data;
  }

  async deleteSession(sessionId: string): Promise<{ success: boolean }> {
    const r = await this.client.delete<{ success: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}`,
    );
    return r.data;
  }

  /**
   * Send a user message to the orchestrator. The endpoint persists
   * the user message, runs the chat loop, and returns both the
   * user message + the assistant reply (M1.7 step 2). Streaming
   * updates arrive via WS events (``chat.delta`` + ``message.added``);
   * the returned ``assistant`` is the authoritative final text.
   *
   * Timeout: a chat turn routinely exceeds the client's 30s default
   * (LLM warmup + tool calls + synthesis can run minutes, bounded by
   * the server's turn timeout). Aborting client-side leaves the turn
   * running server-side with the UI flying blind, so this call gets
   * the full turn budget.
   */
  async sendMessage(
    sessionId: string,
    body: { role: string; content: string },
  ): Promise<{ success: boolean; assistant: SessionMessage }> {
    const r = await this.client.post(
      `/sessions/${encodeURIComponent(sessionId)}/messages`,
      body,
      { timeout: 900_000 },
    );
    return r.data;
  }

  /**
   * Active-turn snapshot (2026-09-10 recovery contract). Returns the
   * snapshot when a turn is active for THIS server process, else
   * ``null`` (the endpoint answers 200 with ``{active: false,
   * turn: null}`` when idle -- no 404 path). Never durable: after a
   * server restart there is no active turn.
   */
  async getActiveTurn(sessionId: string): Promise<TurnSnapshot | null> {
    const r = await this.client.get<{ active: boolean; turn: TurnSnapshot | null }>(
      `/sessions/${encodeURIComponent(sessionId)}/turn`,
    );
    return r.data.active ? (r.data.turn ?? null) : null;
  }

  /**
   * Re-run the turn starting at a past user message. `content` set =
   * edit + resend; omitted = retry. Later messages are flagged
   * superseded server-side; an edit rotates the orchestrator
   * session. Same long timeout as sendMessage.
   */
  async rerunTurn(
    sessionId: string,
    body: { from_message_id: string; content?: string },
  ): Promise<{ success: boolean; assistant: SessionMessage }> {
    const r = await this.client.post(
      `/sessions/${encodeURIComponent(sessionId)}/rerun`,
      body,
      { timeout: 900_000 },
    );
    return r.data;
  }

  /**
   * Stop the live turn (Stop button). Cancels the whole subtree
   * server-side and persists the partial reply as a `cancelled`
   * assistant bubble; the WS `message.added` + status events drive
   * the thread back to idle. 404 when no turn is running (a raced
   * double-tap lands here — not an error worth surfacing).
   */
  async cancelTurn(
    sessionId: string,
  ): Promise<{ success: boolean; assistant: SessionMessage }> {
    const r = await this.client.post(
      `/sessions/${encodeURIComponent(sessionId)}/turn/cancel`,
      {},
      { timeout: 60_000 },
    );
    return r.data;
  }

  // ---- Specialists ----

  async listSpecialists(): Promise<SpecialistSummary[]> {
    const r = await this.client.get<{ specialists: SpecialistSummary[] }>(
      "/specialists",
    );
    return r.data.specialists;
  }

  /** Read one specialist — incl. the orchestrator singleton (its own branch). */
  async getSpecialist(name: string): Promise<SpecialistSummary> {
    const r = await this.client.get<SpecialistSummary>(
      `/specialists/${encodeURIComponent(name)}`,
    );
    return r.data;
  }

  async createSpecialist(
    body: SpecialistCreate,
    scope: "project" | "global" = "project",
  ): Promise<SpecialistSummary> {
    const r = await this.client.post<SpecialistSummary>("/specialists", {
      ...body,
      scope,
    });
    return r.data;
  }

  async deleteSpecialist(
    name: string,
    scope: "project" | "global" = "project",
  ): Promise<{ success: boolean }> {
    const r = await this.client.delete<{ success: boolean }>(
      `/specialists/${encodeURIComponent(name)}?scope=${scope}`,
    );
    return r.data;
  }

  /** Edit a project/global specialist (seeds + orchestrator are API-locked). */
  async updateSpecialist(
    name: string,
    body: {
      description?: string;
      system_prompt?: string;
      role_ref?: string | null;
      harness?: string;
      worktree_policy?: string;
    },
    scope: "project" | "global" = "project",
  ): Promise<SpecialistSummary> {
    const r = await this.client.put<SpecialistSummary>(
      `/specialists/${encodeURIComponent(name)}?scope=${scope}`,
      body,
    );
    return r.data;
  }

  async setSpecialistModel(
    name: string,
    model: string,
  ): Promise<SpecialistSummary> {
    const r = await this.client.put<SpecialistSummary>(
      `/specialists/${encodeURIComponent(name)}/model`,
      { model },
    );
    return r.data;
  }

  // ---- Delegations ----

  async listDelegations(filters?: {
    project_name?: string;
    status?: string;
    parent_task_id?: string;
  }): Promise<Delegation[]> {
    const r = await this.client.get<{ delegations: Delegation[] }>(
      "/delegations",
      { params: filters ?? {} },
    );
    return r.data.delegations;
  }

  /**
   * M1.13 step 5: Children-tab archive surface. ``archived=false``
   * keeps the live list clean (archived rows hidden); the unioned
   * per-project aggregates ride along as ``archived_projects`` so
   * the compact Archived group needs NO extra fetches. Shape mirrors
   * sweave/web/routers/delegations.py ``list_delegations``.
   */
  async listDelegationsWithArchive(): Promise<{
    delegations: Delegation[];
    archived_projects: ArchivedProjectSummary[];
  }> {
    const r = await this.client.get<{
      delegations: Delegation[];
      archived_projects?: ArchivedProjectSummary[];
    }>("/delegations", {
      params: { archived: "false", include_archived: true },
    });
    return {
      delegations: r.data.delegations ?? [],
      archived_projects: r.data.archived_projects ?? [],
    };
  }

  async getDelegation(delegationId: string): Promise<Delegation> {
    const r = await this.client.get<Delegation>(
      `/delegations/${encodeURIComponent(delegationId)}`,
    );
    return r.data;
  }

  async getDelegationDetail(delegationId: string): Promise<DelegationDetail> {
    const r = await this.client.get<DelegationDetail>(
      `/delegations/${encodeURIComponent(delegationId)}/detail`,
    );
    return r.data;
  }

  /**
   * Usage-ledger summary (the Stats page). Computed on read from
   * delegation records + trace token anchors — counts and shapes
   * only, never prompt/response text.
   */
  async getStatsSummary(days = 30): Promise<StatsSummary> {
    const r = await this.client.get<StatsSummary>("/stats/summary", {
      params: { days },
    });
    return r.data;
  }

  /** Promote a ``review`` delegation to ``done`` (M1.4+M1.5 ruling). */
  async promoteDelegation(
    delegationId: string,
  ): Promise<Delegation> {
    const r = await this.client.post<Delegation>(
      `/delegations/${encodeURIComponent(delegationId)}/promote`,
      {},
    );
    return r.data;
  }

  // ---- Escalations (M1.9 step 3) ----

  async getEscalation(
    delegationId: string,
  ): Promise<EscalationRecord | null> {
    try {
      const r = await this.client.get<EscalationRecord>(
        `/delegations/${encodeURIComponent(delegationId)}/escalation`,
      );
      return r.data;
    } catch (err: unknown) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        return null;
      }
      throw err;
    }
  }

  async answerEscalation(
    delegationId: string,
    response: string,
  ): Promise<EscalationRecord> {
    const r = await this.client.post<EscalationRecord>(
      `/delegations/${encodeURIComponent(delegationId)}/answer`,
      { response },
    );
    return r.data;
  }

  /**
   * M1.11 explicit skip (opencode-Esc equivalent). The caller must
   * have shown the system-issued "are you sure?" confirm first;
   * the server rejects unconfirmed skips (409).
   */
  async skipEscalation(
    delegationId: string,
  ): Promise<EscalationRecord> {
    const r = await this.client.post<EscalationRecord>(
      `/delegations/${encodeURIComponent(delegationId)}/skip`,
      { confirmed: true },
    );
    return r.data;
  }

  // ---- Config / models / harnesses / worktrees ----

  async getConfig(): Promise<unknown> {
    const r = await this.client.get("/config");
    return r.data;
  }

  async getModels(): Promise<ModelsConfig> {
    const r = await this.client.get<ModelsConfig>("/models");
    return r.data;
  }

  /** Set the global default model (orchestrator + unset specialists). */
  async setDefaultModel(
    model: string,
  ): Promise<{ success: boolean; model: string; default: string }> {
    const r = await this.client.post<{ success: boolean; model: string; default: string }>(
      "/models",
      { model },
    );
    return r.data;
  }

  /**
   * Regenerate models.yaml from models.dev + the serve overlay.
   * Slow (network fetch + scratch-serve boot, often past the 30s
   * default timeout), so this call carries its own 5-minute budget.
   */
  async regenerateModels(): Promise<{
    success: boolean;
    providers: number;
    models: number;
    added: number;
    removed: number;
    source: string;
    path: string;
  }> {
    const r = await this.client.post<{
      success: boolean;
      providers: number;
      models: number;
      added: number;
      removed: number;
      source: string;
      path: string;
    }>("/models/regenerate", {}, { timeout: 300_000 });
    return r.data;
  }

  async listHarnesses(): Promise<HarnessInfo[]> {
    const r = await this.client.get<{ harnesses: HarnessInfo[] }>("/harnesses");
    return r.data.harnesses;
  }

  // ---- Provider credentials (Sweave-canonical keychain) ----

  /** Universe × availability: every catalog provider + its credential state. */
  async listProviders(): Promise<{ providers: ProviderAvailability[]; pending_imports: PendingImport[] }> {
    const r = await this.client.get<{ providers: ProviderAvailability[]; pending_imports: PendingImport[] }>(
      "/providers",
    );
    return r.data;
  }

  /** Store an API key for a provider (Sweave-canonical; push-through to opencode stores). */
  async setCredential(
    provider: string,
    key: string,
  ): Promise<{ credential: unknown; pushed: string[] }> {
    const r = await this.client.post<{ credential: unknown; pushed: string[] }>(
      "/credentials",
      { provider, key },
    );
    return r.data;
  }

  async deleteCredential(provider: string): Promise<{ success: boolean; provider: string }> {
    const r = await this.client.delete<{ success: boolean; provider: string }>(
      `/credentials/${encodeURIComponent(provider)}`,
    );
    return r.data;
  }

  async importCredentials(
    providers?: string[],
  ): Promise<{ adopted: string[]; skipped: unknown[] }> {
    const r = await this.client.post<{ adopted: string[]; skipped: unknown[] }>(
      "/credentials/import",
      providers?.length ? { providers } : {},
    );
    return r.data;
  }

  /**
   * Converge one diverged provider (the Adopt-All ping-pong fix).
   * `mine` pushes our key to every opencode store; `theirs` adopts
   * the disagreeing store's key and converges the rest onto it.
   */
  async resolveCredential(
    provider: string,
    choice: "mine" | "theirs",
    source?: string,
  ): Promise<{ provider: string; choice: string; key_suffix: string | null; pushed: string[]; adopted_from: string | null }> {
    const r = await this.client.post<{
      provider: string;
      choice: string;
      key_suffix: string | null;
      pushed: string[];
      adopted_from: string | null;
    }>("/credentials/resolve", { provider, choice, ...(source ? { source } : {}) });
    return r.data;
  }

  async syncCredentials(): Promise<unknown> {
    const r = await this.client.post("/credentials/sync", {});
    return r.data;
  }

  async getMemoryBanks(): Promise<{ banks: { id: string; scope: string; name: string }[] }> {
    const r = await this.client.get<{ banks: { id: string; scope: string; name: string }[] }>(
      "/memory/banks",
    );
    return r.data;
  }

  async listWorktrees(): Promise<Worktree[]> {
    const r = await this.client.get<{ worktrees: Worktree[] }>("/worktrees");
    return r.data.worktrees;
  }

  // ---- Memory (recall / retain / reflect) ----

  async recallMemory(query: string, bankId?: string, limit = 10): Promise<unknown> {
    const r = await this.client.post("/memory/recall", { query, bank_id: bankId, limit });
    return r.data;
  }

  async retainMemory(content: string, bankId?: string, tags?: string[]): Promise<unknown> {
    const r = await this.client.post("/memory/retain", { content, bank_id: bankId, tags });
    return r.data;
  }

  // ---- Filesystem browser (project path picker) ----

  async listDrives(): Promise<{ name: string; path: string; is_dir: boolean }[]> {
    const r = await this.client.get<{ drives: { name: string; path: string; is_dir: boolean }[] }>(
      "/fs/drives",
    );
    return r.data.drives;
  }

  async listDirectory(
    path: string,
  ): Promise<{ path: string; parent: string | null; entries: { name: string; path: string; is_dir: boolean }[] }> {
    const r = await this.client.get<{ path: string; parent: string | null; entries: { name: string; path: string; is_dir: boolean }[] }>(
      "/fs/list",
      { params: { path } },
    );
    return r.data;
  }

  async validatePath(path: string): Promise<{ valid: boolean; path?: string; name?: string; error?: string }> {
    const r = await this.client.post<{ valid: boolean; path?: string; name?: string; error?: string }>(
      "/fs/validate",
      { path },
    );
    return r.data;
  }

  async createDirectory(parent: string, name: string): Promise<{ success: boolean; path: string; name: string }> {
    const r = await this.client.post<{ success: boolean; path: string; name: string }>(
      "/fs/create",
      { path: parent, name },
    );
    return r.data;
  }
}

export const api = new ApiClient();

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
  Delegation,
  DelegationDetail,
  EscalationRecord,
  HarnessInfo,
  ModelsConfig,
  ProjectCreate,
  ProjectSummary,
  SessionCreate,
  SessionDetail,
  SessionSummary,
  SpecialistCreate,
  SpecialistSummary,
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

  /**
   * Send a user message to the orchestrator. The endpoint persists
   * the user message, runs the chat loop, and returns both the
   * user message + the assistant reply (M1.7 step 2). Streaming
   * updates arrive via WS events (``chat.delta`` + ``message.added``);
   * the returned ``assistant`` is the authoritative final text.
   */
  async sendMessage(
    sessionId: string,
    body: { role: "user" | "assistant" | "system" | "tool"; content: string },
  ): Promise<{ success: boolean; message: { id: string; role: string; content: string; timestamp: string }; assistant?: { id: string; role: string; content: string; metadata?: Record<string, unknown> } | null }> {
    const r = await this.client.post(
      `/sessions/${encodeURIComponent(sessionId)}/messages`,
      body,
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

  // ---- Config / models / harnesses / worktrees ----

  async getConfig(): Promise<unknown> {
    const r = await this.client.get("/config");
    return r.data;
  }

  async getModels(): Promise<ModelsConfig> {
    const r = await this.client.get<{ roles: ModelsConfig["roles"] }>("/models");
    return { roles: r.data.roles };
  }

  async listHarnesses(): Promise<HarnessInfo[]> {
    const r = await this.client.get<{ harnesses: HarnessInfo[] }>("/harnesses");
    return r.data.harnesses;
  }

  async listWorktrees(): Promise<Worktree[]> {
    const r = await this.client.get<{ worktrees: Worktree[] }>("/worktrees");
    return r.data.worktrees;
  }
}

export const api = new ApiClient();

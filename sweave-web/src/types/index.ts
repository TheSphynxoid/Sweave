// Core domain types

export interface Project {
  name: string;
  path: string;
  description: string;
  created_at: string;
  updated_at: string;
  default_harness: string;
  memory_bank: string;
  model_overrides: Record<string, string>;
  routing_rules: RoutingRule[];
}

export interface Session {
  id: string;
  project_name: string;
  name: string;
  created_at: string;
  updated_at: string;
  active_agent: string | null;
  current_task: string | null;
  context: Record<string, any>;
  memory_bank: string;
}

export interface RoutingRule {
  pattern: string;
  agent: string;
  model?: string;
}

export interface Agent {
  name: string;
  role: string;
  model: string;
  system_prompt: string;
  description: string;
  builtin: boolean;
  dynamic: boolean;
  harness?: string;
  tools?: string[];
}

export interface TaskRequest {
  task: string;
  agent?: string;
  model?: string;
}

export interface TaskResponse {
  success: boolean;
  agent: string;
  task_id: string;
  output: string;
  error?: string;
}

export interface RoutingDecision {
  agent: string;
  model: string;
  confidence: number;
  reasoning: string;
  matched_rule?: string;
}

export interface HarnessInfo {
  name: string;
  display_name: string;
  command: string;
  version: string;
  providers: string[];
  models: string[];
}

export interface MemoryEntry {
  content: string;
  tags: string[];
  metadata: Record<string, any>;
}

export interface MemoryResult {
  memories?: MemoryEntry[];
  reflection?: string;
  result?: string;
  error?: string;
}

export interface Worktree {
  path: string;
  branch: string;
  task_id: string;
  agent: string;
  created_at: string;
  pr_url?: string;
}

// API Response types
export interface ApiResponse<T> {
  success?: boolean;
  data?: T;
  error?: string;
  detail?: string;
}

export interface ProjectsResponse {
  projects: ProjectSummary[];
}

export interface ProjectSummary {
  name: string;
  path: string;
  description: string;
  created_at: string;
  updated_at: string;
  active: boolean;
}

export interface SessionsResponse {
  sessions: SessionSummary[];
}

export interface SessionSummary {
  id: string;
  name: string;
  project_name: string;
  created_at: string;
  updated_at: string;
  active_agent: string | null;
  current_task: string | null;
}

// WebSocket event types
export interface WSEvent {
  event: string;
  data: any;
  timestamp: string;
}

// UI State types
export interface UIState {
  sidebarOpen: boolean;
  activeView: 'dashboard' | 'projects' | 'sessions' | 'agents' | 'tasks' | 'settings';
  selectedProject: string | null;
  selectedSession: string | null;
  notifications: Notification[];
}

export interface Notification {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info' | 'warning';
}
import axios, { AxiosInstance, InternalAxiosRequestConfig } from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

class ApiClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: 30000,
    });

    this.client.interceptors.request.use(
      (config: InternalAxiosRequestConfig) => {
        // Add any auth headers if needed
        return config;
      },
      (error) => Promise.reject(error)
    );

    this.client.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error.response?.status === 401) {
          // Handle unauthorized
          console.error('Unauthorized access');
        }
        return Promise.reject(error);
      }
    );
  }

  // Projects
  async listProjects() {
    const response = await this.client.get<{ projects: any[] }>('/projects');
    return response.data.projects;
  }

  async createProject(data: { name: string; path: string; description?: string }) {
    const response = await this.client.post('/projects', data);
    return response.data;
  }

  async getProject(name: string) {
    const response = await this.client.get(`/projects/${name}`);
    return response.data;
  }

  async setActiveProject(name: string) {
    const response = await this.client.post(`/projects/${name}/active`);
    return response.data;
  }

  async deleteProject(name: string) {
    const response = await this.client.delete(`/projects/${name}`);
    return response.data;
  }

  async getActiveProject() {
    const response = await this.client.get('/projects/active');
    return response.data;
  }

  // Sessions
  async listSessions(projectName?: string) {
    const response = await this.client.get<{ sessions: any[] }>('/sessions', {
      params: { project_name: projectName },
    });
    return response.data.sessions;
  }

  async createSession(data: { name: string; project_name?: string }) {
    const response = await this.client.post('/sessions', data);
    return response.data;
  }

  async setActiveSession(sessionId: string) {
    const response = await this.client.post(`/sessions/${sessionId}/active`);
    return response.data;
  }

  async getActiveSession() {
    const response = await this.client.get('/sessions/active');
    return response.data;
  }

  async getActiveProject() {
    const response = await this.client.get('/projects/active');
    return response.data;
  }

  // Agents
  async listAgents() {
    const response = await this.client.get('/agents');
    return response.data;
  }

  async createAgent(data: any) {
    const response = await this.client.post('/agents', data);
    return response.data;
  }

  async getAgent(name: string) {
    const response = await this.client.get(`/agents/${name}`);
    return response.data;
  }

  async updateAgent(name: string, data: any) {
    const response = await this.client.put(`/agents/${name}`, data);
    return response.data;
  }

  async deleteAgent(name: string) {
    const response = await this.client.delete(`/agents/${name}`);
    return response.data;
  }

  // Tasks
  async runTask(data: { task: string; agent?: string; model?: string }) {
    const response = await this.client.post('/tasks', data);
    return response.data;
  }

  async routeTask(task: string) {
    const response = await this.client.post('/route', { task });
    return response.data;
  }

  // Models
  async listModels() {
    const response = await this.client.get('/models');
    return response.data;
  }

  async setModel(role: string, model: string) {
    const response = await this.client.post('/models', { role, model });
    return response.data;
  }

  // Rules
  async listRules() {
    const response = await this.client.get('/rules');
    return response.data;
  }

  async addRule(data: { pattern: string; agent: string; model?: string }) {
    const response = await this.client.post('/rules', data);
    return response.data;
  }

  // Config
  async getConfig() {
    const response = await this.client.get('/config');
    return response.data;
  }

  // Harnesses
  async listHarnesses() {
    const response = await this.client.get('/harnesses');
    return response.data;
  }

  // Worktrees
  async listWorktrees() {
    const response = await this.client.get('/worktrees');
    return response.data.worktrees;
  }

  async cleanWorktrees() {
    const response = await this.client.post('/worktrees/clean');
    return response.data;
  }

  // Memory
  async recallMemory(query: string, bankId?: string, limit?: number) {
    const response = await this.client.post('/memory/recall', {
      query,
      bank_id: bankId,
      limit,
    });
    return response.data;
  }

  async retainMemory(content: string, bankId?: string, tags?: string[]) {
    const response = await this.client.post('/memory/retain', {
      content,
      bank_id: bankId,
      tags,
    });
    return response.data;
  }

  async reflectMemory(query: string, bankId?: string) {
    const response = await this.client.post('/memory/reflect', {
      query,
      bank_id: bankId,
    });
    return response.data;
  }
}

export const api = new ApiClient();
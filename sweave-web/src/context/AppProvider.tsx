import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api } from '../api/client';
import type { ProjectSummary, SessionSummary, UIState, Notification } from '../types';

interface AppContextType extends UIState {
  projects: ProjectSummary[];
  sessions: any[];
  activeProject: ProjectSummary | null;
  activeSession: any | null;
  loading: boolean;
  error: string | null;
  
  // Actions
  fetchProjects: () => Promise<void>;
  fetchSessions: (projectName?: string) => Promise<void>;
  createProject: (data: { name: string; path: string; description?: string }) => Promise<void>;
  setActiveProject: (name: string) => Promise<void>;
  deleteProject: (name: string) => Promise<void>;
  createSession: (data: { name: string; project_name?: string }) => Promise<void>;
  setActiveSession: (sessionId: string) => Promise<void>;
  fetchSessionsForProject: (projectName: string) => Promise<void>;
  setSidebarOpen: (open: boolean) => void;
  setActiveView: (view: UIState['activeView']) => void;
  addNotification: (notification: Omit<Notification, 'id'>) => void;
  removeNotification: (id: number) => void;
  loadInitialData: () => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [activeProject, setActiveProjectState] = useState<any>(null);
  const [activeSession, setActiveSessionState] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeView, setActiveView] = useState<UIState['activeView']>('dashboard');
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const fetchProjects = async () => {
    try {
      const data = await api.listProjects();
      setProjects(data);
      
      // Set active project from loaded data
      const active = data.find(p => p.active);
      if (active && !activeProject) {
        setActiveProjectState(active);
      }
    } catch (err) {
      console.error('Failed to fetch projects:', err);
      setError('Failed to load projects');
    }
  };

  const fetchSessions = async (projectName?: string) => {
    try {
      const data = await api.listSessions(projectName);
      setSessions(data);
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    }
  };

  const createProject = async (data: { name: string; path: string; description?: string }) => {
    setLoading(true);
    setError(null);
    try {
      await api.createProject(data);
      await fetchProjects();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create project');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const setActiveProject = async (name: string) => {
    setLoading(true);
    try {
      await api.setActiveProject(name);
      const project = projects.find(p => p.name === name);
      if (project) {
        setActiveProjectState(project);
        // Fetch sessions for this project
        await fetchSessions(name);
      }
    } catch (err) {
      setError('Failed to set active project');
    } finally {
      setLoading(false);
    }
  };

  const deleteProject = async (name: string) => {
    setLoading(true);
    try {
      await api.deleteProject(name);
      await fetchProjects();
    } catch (err) {
      setError('Failed to delete project');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const createSession = async (data: { name: string; project_name?: string }) => {
    setLoading(true);
    try {
      await api.createSession(data);
      await fetchSessions(activeProject?.name);
    } catch (err) {
      setError('Failed to create session');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const setActiveSession = async (sessionId: string) => {
    try {
      await api.setActiveSession(sessionId);
      const session = sessions.find(s => s.id === sessionId);
      if (session) {
        setActiveSessionState(session);
      }
    } catch (err) {
      setError('Failed to set active session');
    }
  };

  const fetchSessionsForProject = async (projectName: string) => {
    await fetchSessions(projectName);
  };

  const addNotification = (notification: Omit<Notification, 'id'>) => {
    const id = Date.now();
    setNotifications(prev => [...prev, { ...notification, id }]);
    // Auto-remove after 5 seconds
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== id));
    }, 5000);
  };

  const removeNotification = (id: number) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  };

  const loadInitialData = async () => {
    setLoading(true);
    try {
      await Promise.all([
        fetchProjects(),
        api.getActiveProject().then(p => {
          if (p) setActiveProjectState(p);
        }),
      ]);
      
      if (activeProject) {
        await fetchSessions(activeProject.name);
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadInitialData();
  }, []);

  const value: AppContextType = {
    projects,
    sessions,
    activeProject,
    activeSession,
    loading,
    error,
    sidebarOpen,
    activeView,
    notifications,
    fetchProjects,
    fetchSessions,
    createProject,
    setActiveProject,
    deleteProject,
    createSession,
    setActiveSession,
    fetchSessionsForProject,
    setSidebarOpen,
    setActiveView,
    addNotification,
    removeNotification,
    loadInitialData,
  };

  return (
    <AppContext.Provider value={value}>
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
}
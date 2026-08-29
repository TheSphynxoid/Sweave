import { useApp } from '../context/AppProvider';
import {
  LayoutDashboard,
  FolderGit2,
  MessageSquare,
  Bot,
  Settings,
  ChevronLeft,
  ChevronRight,
  Plus,
  FolderOpen,
} from 'lucide-react';
import { useState } from 'react';
import { useApp } from '../context/AppProvider';
import { cn } from '../utils/cn';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'projects', label: 'Projects', icon: FolderGit2 },
  { id: 'sessions', label: 'Sessions', icon: MessageSquare },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

export function Sidebar() {
  const {
    sidebarOpen,
    setSidebarOpen,
    activeView,
    setActiveView,
    projects,
    activeProject,
    sessions,
    activeSession,
    setActiveProject,
    createProject,
  } = useApp();
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectPath, setNewProjectPath] = useState('');
  const [newProjectDesc, setNewProjectDesc] = useState('');

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjectName || !newProjectPath) return;

    try {
      await createProject({
        name: newProjectName,
        path: newProjectPath,
        description: newProjectDesc,
      });
      setShowCreateProject(false);
      setNewProjectName('');
      setNewProjectPath('');
      setNewProjectDesc('');
    } catch (err) {
      console.error('Failed to create project:', err);
    }
  };

  const renderProjects = () => {
    if (projects.length === 0) {
      return (
        <div className="text-center py-8 text-muted-foreground text-sm">
          <FolderOpen className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>No projects yet</p>
          <button
            onClick={() => setShowCreateProject(true)}
            className="mt-2 text-primary hover:underline text-sm"
          >
            Create your first project
          </button>
        </div>
      );
    }

    return (
      <ul className="space-y-1">
        {projects.map((project) => (
          <li key={project.name}>
            <button
              onClick={() => setActiveProject(project.name)}
              className={cn(
                'w-full px-3 py-2 rounded-lg text-left text-sm transition-colors flex items-center gap-2',
                activeProject?.name === project.name
                  ? 'bg-primary/10 text-primary border-l-2 border-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <FolderOpen className={cn('w-4 h-4 flex-shrink-0', activeProject?.name === project.name ? 'text-primary' : 'text-muted-foreground')} />
              <span className="truncate">{project.name}</span>
              {project.active && <span className="ml-auto w-1.5 h-1.5 bg-primary rounded-full" />}
            </button>
          </li>
        ))}
      </ul>
    );
  };

  const renderSessions = () => {
    if (sessions.length === 0) {
      return (
        <li className="px-2 py-4 text-center text-muted-foreground text-sm">
          No sessions for this project
        </li>
      );
    }

    const sessionItems = sessions.map((session) => (
      <li key={session.id}>
        <button
          className={cn(
            'w-full px-3 py-2 rounded-lg text-left text-sm transition-colors flex items-center gap-2',
            activeSession?.id === session.id
              ? 'bg-primary/10 text-primary'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          )}
        >
          <MessageSquare className="w-4 h-4 flex-shrink-0" />
          <span className="truncate">{session.name}</span>
        </button>
      </li>
    ));

    return (
      <ul className="space-y-1">
        {sessionItems}
      </ul>
    );
  };

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjectName || !newProjectPath) return;

    try {
      await createProject({
        name: newProjectName,
        path: newProjectPath,
        description: newProjectDesc,
      });
      setShowCreateProject(false);
      setNewProjectName('');
      setNewProjectPath('');
      setNewProjectDesc('');
    } catch (err) {
      console.error('Failed to create project:', err);
    }
  };

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 h-full bg-card border-r border-border transition-all duration-200 z-40 flex flex-col',
        sidebarOpen ? 'w-64' : 'w-20'
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between h-16 px-4 border-b border-border">
        {sidebarOpen && (
          <h1 className="text-lg font-bold text-foreground flex items-center gap-2">
            <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center">
              <svg className="w-5 h-5 text-primary-foreground" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
            </div>
            <span>Sweave</span>
          </h1>
        )}
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="p-2 rounded-lg hover:bg-muted transition-colors"
          aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {sidebarOpen ? <ChevronLeft className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
        </button>
      </div>

      {sidebarOpen && (
        <div>
          {/* New Project Form */}
          {showCreateProject && (
            <form onSubmit={handleCreateProject} className="p-4 space-y-3 border-b border-border">
              <div className="space-y-2">
                <label className="block text-sm font-medium text-muted-foreground">Name</label>
                <input
                  type="text"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  className="w-full px-3 py-2 bg-input border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="my-project"
                  required
                />
              </div>
              <div className="space-y-2">
                <label className="block text-sm font-medium text-muted-foreground">Path</label>
                <input
                  type="text"
                  value={newProjectPath}
                  onChange={(e) => setNewProjectPath(e.target.value)}
                  className="w-full px-3 py-2 bg-input border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="/home/user/projects/my-app"
                  required
                />
              </div>
              <div className="space-y-2">
                <label className="block text-sm font-medium text-muted-foreground">Description (optional)</label>
                <textarea
                  value={newProjectDesc}
                  onChange={(e) => setNewProjectDesc(e.target.value)}
                  className="w-full px-3 py-2 bg-input border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  rows={2}
                  placeholder="Project description"
                />
              </div>
              <div className="flex gap-2">
                <button type="submit" className="flex-1 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90">
                  Create
                </button>
                <button
                  type="button"
                  onClick={() => { setShowCreateProject(false); setNewProjectName(''); setNewProjectPath(''); setNewProjectDesc(''); }}
                  className="px-3 py-2 bg-secondary text-secondary-foreground rounded-md text-sm hover:bg-secondary/80"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          {/* Projects List */}
          <nav className="flex-1 overflow-y-auto p-3 space-y-1" aria-label="Projects">
            <div className="flex items-center justify-between px-2 py-2">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Projects</h2>
              <button
                onClick={() => setShowCreateProject(true)}
                className="p-1.5 rounded hover:bg-muted transition-colors"
                aria-label="Create new project"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>

            {renderProjects()}
          </nav>

          {/* Sessions List */}
          <nav className="p-3 space-y-1 border-t border-border" aria-label="Sessions">
            <h2 className="px-2 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Sessions</h2>
            {renderSessions()}
          </nav>
        </div>
      )}
    </aside>
  );
}
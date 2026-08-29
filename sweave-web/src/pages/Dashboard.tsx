import { useApp } from '../context/AppProvider';
import { FolderGit2, MessageSquare, Bot, Plus, Play, FolderOpen, MessageSquare as MsgIcon, Bot as BotIcon } from 'lucide-react';
import { cn } from '../utils/cn';

const statCards = [
  { label: 'Projects', value: 'projects.length', icon: FolderOpen, color: 'bg-blue-500/10 text-blue-500' },
  { label: 'Sessions', value: 'sessions.length', icon: MsgIcon, color: 'bg-green-500/10 text-green-500' },
  { label: 'Agents', value: '4', icon: BotIcon, color: 'bg-purple-500/10 text-purple-500' },
  { label: 'Tasks Today', value: '0', icon: Play, color: 'bg-orange-500/10 text-orange-500' },
];

const quickActions = [
  { label: 'New Project', icon: FolderGit2, action: 'create-project' },
  { label: 'New Session', icon: MessageSquare, action: 'create-session' },
  { label: 'Run Task', icon: Play, action: 'run-task' },
  { label: 'Manage Agents', icon: Bot, action: 'manage-agents' },
];

export function Dashboard() {
  const { projects, sessions, activeProject, activeSession, setActiveView, addNotification } = useApp();

  const handleQuickAction = (action: string) => {
    switch (action) {
      case 'create-project':
        setActiveView('projects');
        break;
      case 'create-session':
        if (!activeProject) {
          setActiveView('projects');
          addNotification({ message: 'Select a project first', type: 'warning' });
        } else {
          setActiveView('sessions');
        }
        break;
      case 'run-task':
        setActiveView('tasks');
        break;
      case 'manage-agents':
        setActiveView('agents');
        break;
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground">
            Welcome back! Here's what's happening with your projects.
          </p>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map((stat, index) => (
          <div key={index} className="bg-card border border-border rounded-lg p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">{stat.label}</p>
                <p className="text-3xl font-bold mt-1">
                  {eval(stat.value)}
                </p>
              </div>
              <div className={cn('w-12 h-12 rounded-lg flex items-center justify-center', stat.color)}>
                <stat.icon className="w-6 h-6" />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Quick Actions */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {quickActions.map((action, index) => (
            <button
              key={index}
              onClick={() => handleQuickAction(action.action)}
              className={cn(
                'flex items-center gap-3 px-4 py-3 rounded-lg border border-border hover:bg-muted transition-colors text-left',
                !activeProject && action.action === 'create-session' && 'opacity-50 cursor-not-allowed'
              )}
              disabled={!activeProject && action.action === 'create-session'}
            >
              <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center">
                <action.icon className="w-5 h-5 text-primary" />
              </div>
              <span className="font-medium">{action.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Active Project */}
        <div className="bg-card border border-border rounded-lg p-6">
          <h2 className="text-lg font-semibold mb-4">Active Project</h2>
          {activeProject ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center">
                  <FolderOpen className="w-6 h-6 text-primary" />
                </div>
                <div>
                  <h3 className="font-semibold">{activeProject.name}</h3>
                  <p className="text-sm text-muted-foreground truncate">{activeProject.path}</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-muted-foreground">Sessions</p>
                  <p className="font-semibold">{sessions.length}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Updated</p>
                  <p className="font-semibold">{new Date(activeProject.updated_at).toLocaleDateString()}</p>
                </div>
              </div>
              <button className="w-full mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors">
                Open Project
              </button>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <FolderGit2 className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No active project</p>
              <button 
                className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
                onClick={() => setActiveView('projects')}
              >
                Create or Select a Project
              </button>
            </div>
          )}
        </div>

        {/* Active Session */}
        <div className="bg-card border border-border rounded-lg p-6">
          <h2 className="text-lg font-semibold mb-4">Active Session</h2>
          {activeSession ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-green-500/10 rounded-lg flex items-center justify-center">
                  <MessageSquare className="w-6 h-6 text-green-500" />
                </div>
                <div>
                  <h3 className="font-semibold">{activeSession.name}</h3>
                  <p className="text-sm text-muted-foreground">{activeSession.project_name}</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-muted-foreground">Created</p>
                  <p className="font-semibold">{new Date(activeSession.created_at).toLocaleDateString()}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Status</p>
                  <p className="font-semibold text-green-500">Active</p>
                </div>
              </div>
              <button className="w-full mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors">
                Continue Session
              </button>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No active session</p>
              <button 
                className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
                onClick={() => setActiveView('sessions')}
              >
                Create a Session
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Fix missing import
import { useApp } from '../context/AppProvider';
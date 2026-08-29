import { useApp } from '../context/AppProvider';
import { Plus, Send, MessageSquare, Bot, X, RotateCcw, Play, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { cn } from '../utils/cn';

export function Tasks() {
  const { activeProject, activeSession, activeView, setActiveView, addNotification } = useApp();
  const [taskInput, setTaskInput] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [executing, setExecuting] = useState(false);
  const [routingPreview, setRoutingPreview] = useState<any>(null);
  const [taskHistory, setTaskHistory] = useState<any[]>([]);

  const agents = [
    { id: 'orchestrator', name: 'Orchestrator', role: 'Coordinates tasks', defaultModel: 'deepseek-flash' },
    { id: 'backend', name: 'Backend', role: 'APIs, databases, auth', defaultModel: 'qwen2.5-coder' },
    { id: 'frontend', name: 'Frontend', role: 'UI, React, Vue, CSS', defaultModel: 'hy3' },
    { id: 'reviewer', name: 'Reviewer', role: 'Code review, security', defaultModel: 'claude-3.5-sonnet' },
  ];

  const handleExecute = async () => {
    if (!taskInput.trim()) return;
    if (!activeSession) {
      addNotification({ message: 'Select a session first', type: 'warning' });
      return;
    }

    setExecuting(true);
    setRoutingPreview(null);
    try {
      // Get routing preview first
      const routeRes = await fetch('/api/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: taskInput }),
      });
      const route = await routeRes.json();
      setRoutingPreview(route);

      // Execute task
      const res = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task: taskInput,
          agent: selectedAgent || undefined,
          model: selectedModel || undefined,
        }),
      });
      const result = await res.json();

      addNotification({ 
        message: `Task ${result.success ? 'completed' : 'failed'}`, 
        type: result.success ? 'success' : 'error' 
      });

      // Add to history
      setTaskHistory(prev => [{
        id: result.task_id,
        task: taskInput,
        agent: result.agent,
        model: result.model || route.model,
        success: result.success,
        output: result.output,
        error: result.error,
        time: new Date().toLocaleTimeString(),
        routing: route,
      }, ...prev.slice(0, 19)]);

      setTaskInput('');
    } catch (err) {
      addNotification({ message: 'Failed to execute task', type: 'error' });
    } finally {
      setExecuting(false);
    }
  };

  const getRoutingPreview = async () => {
    if (!taskInput.trim()) {
      setRoutingPreview(null);
      return;
    }
    try {
      const res = await fetch('/api/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: taskInput }),
      });
      const route = await res.json();
      setRoutingPreview(route);
    } catch (err) {
      console.error('Routing preview failed:', err);
    }
  };

  // Debounce routing preview
  const debouncedPreview = (() => {
    let timeout: NodeJS.Timeout;
    return (value: string) => {
      clearTimeout(timeout);
      setTaskInput(value);
      timeout = setTimeout(() => getRoutingPreview(), 500);
    };
  })();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Task Execution</h1>
          <p className="text-muted-foreground">Run tasks through the orchestrator</p>
        </div>
      </div>

      {/* Task Form */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-semibold mb-4">Execute Task</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-2">Task Description</label>
            <textarea
              value={taskInput}
              onChange={(e) => debouncedPreview(e.target.value)}
              rows={4}
              className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              placeholder="Describe the task...&#10;&#10;Example: Build a REST API with JWT authentication for user management"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-2">Agent</label>
              <select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
                className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">Auto-route (recommended)</option>
                {agents.map(agent => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name} ({agent.role})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-2">Model Override</label>
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">Use agent default</option>
                {agents.map(agent => (
                  <option key={agent.id} value={agent.defaultModel}>
                    {agent.defaultModel} ({agent.name})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-2">&nbsp;</label>
              <button
                onClick={handleExecute}
                disabled={executing || !taskInput.trim()}
                className="w-full px-4 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {executing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Executing...
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4" />
                    Execute Task
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Routing Preview */}
      {routingPreview && (
        <div className="bg-card border border-border rounded-lg p-6 animate-fade-in">
          <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <RotateCcw className="w-5 h-5" />
            Routing Preview
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-muted/50 rounded-lg p-4">
              <p className="text-xs text-muted-foreground mb-1">Agent</p>
              <p className="font-semibold">{routingPreview.agent}</p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <p className="text-xs text-muted-foreground mb-1">Model</p>
              <p className="font-semibold font-mono">{routingPreview.model}</p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <p className="text-xs text-muted-foreground mb-1">Confidence</p>
              <p className="font-semibold">{Math.round(routingPreview.confidence * 100)}%</p>
            </div>
            <div className="bg-muted/50 rounded-lg p-4">
              <p className="text-xs text-muted-foreground mb-1">Reasoning</p>
              <p className="font-semibold text-sm truncate">{routingPreview.reasoning}</p>
            </div>
          </div>
          {routingPreview.matched_rule && (
            <p className="mt-3 text-xs text-muted-foreground">
              Matched rule: <code className="bg-muted px-1.5 py-0.5 rounded text-xs">{routingPreview.matched_rule}</code>
            </p>
          )}
        </div>
      )}

      {/* Task History */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-lg font-semibold">Task History</h3>
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Search tasks..."
              className="bg-input border border-border rounded-lg px-4 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-xs text-muted-foreground uppercase tracking-wider bg-muted/50">
                <th className="px-6 py-3">Task</th>
                <th className="px-6 py-3">Agent</th>
                <th className="px-6 py-3">Model</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Time</th>
                <th className="px-6 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {taskHistory.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-muted-foreground">
                    <MessageSquare className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>No tasks executed yet</p>
                    <p className="text-sm">Execute your first task above</p>
                  </td>
                </tr>
              ) : (
                taskHistory.map((task) => (
                  <tr key={task.id} className="hover:bg-muted/50">
                    <td className="px-6 py-4">
                      <p className="font-mono text-sm text-muted-foreground truncate max-w-xs">{task.task}</p>
                    </td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-0.5 bg-primary/10 text-primary rounded text-xs">{task.agent}</span>
                    </td>
                    <td className="px-6 py-4">
                      <span className="font-mono text-sm">{task.model}</span>
                    </td>
                    <td className="px-6 py-4">
                      <span className={cn('px-2 py-1 text-xs rounded-full', task.success ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500')}>
                        {task.success ? 'Success' : 'Failed'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-muted-foreground">{task.time}</td>
                    <td className="px-6 py-4">
                      <button className="text-primary hover:text-primary/80 text-sm">View</button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
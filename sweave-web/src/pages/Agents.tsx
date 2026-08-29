import { useApp } from '../context/AppProvider';
import { Plus, Bot, Edit, Trash2, Shield, Cpu, Zap, Settings as SettingsIcon } from 'lucide-react';
import { useState } from 'react';
import { cn } from '../utils/cn';

const builtInAgents = [
  { id: 'orchestrator', name: 'Orchestrator', role: 'Coordinates tasks and delegates to specialists', defaultModel: 'deepseek-flash', color: 'bg-purple-500/10 text-purple-500' },
  { id: 'backend', name: 'Backend Specialist', role: 'APIs, databases, authentication, server logic', defaultModel: 'qwen2.5-coder', color: 'bg-blue-500/10 text-blue-500' },
  { id: 'frontend', name: 'Frontend Specialist', role: 'UI components, React/Vue/Svelte, styling', defaultModel: 'hy3', color: 'bg-green-500/10 text-green-500' },
  { id: 'reviewer', name: 'Code Reviewer', role: 'Code review, security audit, quality checks', defaultModel: 'claude-3.5-sonnet', color: 'bg-orange-500/10 text-orange-500' },
];

export function Agents() {
  const { addNotification } = useApp();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingAgent, setEditingAgent] = useState<any>(null);
  const [formData, setFormData] = useState({
    name: '',
    role: '',
    model: '',
    system_prompt: '',
    description: '',
    tools: '',
    harness: 'opencode',
  });

  const models = [
    'deepseek-flash', 'deepseek-coder', 'qwen2.5-coder', 'codellama', 'gpt-4o',
    'claude-3.5-sonnet', 'claude-3.5-haiku', 'hy3', 'llama3.1:70b'
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.role || !formData.model || !formData.system_prompt) return;

    try {
      if (editingAgent) {
        // TODO: implement update
        addNotification({ message: 'Agent updated', type: 'success' });
      } else {
        // TODO: implement create agent API
        addNotification({ message: 'Agent created', type: 'success' });
      }
      setShowCreateModal(false);
      setEditingAgent(null);
      setFormData({ name: '', role: '', model: '', system_prompt: '', description: '', tools: '', harness: 'opencode' });
    } catch (err) {
      console.error('Failed to save agent:', err);
    }
  };

  const handleDelete = (name: string) => {
    if (!confirm(`Delete agent "${name}"?`)) return;
    // TODO: implement delete
    addNotification({ message: 'Agent deleted', type: 'success' });
  };

  const openCreateModal = () => {
    setEditingAgent(null);
    setFormData({ name: '', role: '', model: '', system_prompt: '', description: '', tools: '', harness: 'opencode' });
    setShowCreateModal(true);
  };

  const openEditModal = (agent: any) => {
    setEditingAgent(agent);
    setFormData({
      name: agent.name,
      role: agent.role,
      model: agent.model,
      system_prompt: agent.system_prompt,
      description: agent.description,
      tools: agent.tools?.join(', ') || '',
      harness: agent.harness || 'opencode',
    });
    setShowCreateModal(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agents</h1>
          <p className="text-muted-foreground">Manage specialist agents</p>
        </div>
        <button onClick={openCreateModal} className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2">
          <Plus className="w-4 h-4" />
          Create Agent
        </button>
      </div>

      {/* Built-in Agents */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Shield className="w-5 h-5 text-gray-500" />
          Built-in Specialists
        </h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {builtInAgents.map((agent) => (
            <div key={agent.id} className="bg-card border border-border rounded-xl p-6 hover:border-primary/50 transition-colors">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center', agent.color)}>
                    {agent.id === 'orchestrator' && <Zap className="w-5 h-5" />}
                    {agent.id === 'backend' && <Cpu className="w-5 h-5" />}
                    {agent.id === 'frontend' && <SettingsIcon className="w-5 h-5" />}
                    {agent.id === 'reviewer' && <Shield className="w-5 h-5" />}
                  </div>
                  <div>
                    <h4 className="font-semibold">{agent.name}</h4>
                    <p className="text-xs text-muted-500">Built-in</p>
                  </div>
                </div>
              </div>
              <p className="text-sm text-muted-foreground mb-4">{agent.role}</p>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Model</span>
                  <span className="font-mono text-primary">{agent.defaultModel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Harness</span>
                  <span className="font-mono">opencode</span>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-border flex gap-2">
                <button className="flex-1 px-3 py-2 bg-muted hover:bg-muted/80 rounded text-sm text-center">View Details</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Dynamic Agents */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Plus className="w-5 h-5" />
            Custom Agents
          </h2>
          <button onClick={() => { setEditingAgent(null); setFormData({ name: '', role: '', model: '', system_prompt: '', description: '', tools: '', harness: 'opencode' }); setShowCreateModal(true); }} className="px-3 py-1.5 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 text-sm flex items-center gap-1">
            <Plus className="w-4 h-4" />
            Add Agent
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {/* Empty state for dynamic agents */}
          <div className="bg-card border border-border rounded-xl p-8 text-center col-span-full md:col-span-2 lg:col-span-3">
            <Bot className="w-16 h-16 mx-auto mb-4 opacity-50" />
            <h3 className="text-lg font-semibold mb-2">No custom agents yet</h3>
            <p className="text-muted-foreground mb-6">Create custom agents for specialized tasks</p>
            <button onClick={() => { setEditingAgent(null); setFormData({ name: '', role: '', model: '', system_prompt: '', description: '', tools: '', harness: 'opencode' }); setShowCreateModal(true); }} className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2 mx-auto">
              <Plus className="w-4 h-4" />
              Create Your First Agent
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Fix missing import
import { Shield, Cpu, Zap, Settings as SettingsIcon, Bot } from 'lucide-react';
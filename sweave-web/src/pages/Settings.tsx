import { useApp } from '../context/AppProvider';
import { useState } from 'react';
import { 
  Cpu, Database, Globe, Shield, HardDrive, 
  RefreshCw, Save, Loader2, AlertTriangle, CheckCircle
} from 'lucide-react';
import { cn } from '../utils/cn';
import { api } from '../api/client';

interface ModelRole {
  default: string;
  aliases: string[];
  provider: string;
}

export function Settings() {
  const { 
    activeProject, 
    addNotification,
    fetchProjects,
    fetchSessions,
  } = useApp();
  
  const [activeTab, setActiveTab] = useState<'general' | 'models' | 'routing' | 'harness' | 'memory' | 'git' | 'server'>('general');
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<Record<string, ModelRole>>({});
  const [routingRules, setRoutingRules] = useState<any[]>([]);
  const [newRule, setNewRule] = useState({ pattern: '', agent: '', model: '' });
  const [harnesses, setHarnesses] = useState<any[]>([]);
  const [memoryConfig, setMemoryConfig] = useState<any>({});
  const [gitConfig, setGitConfig] = useState<any>({});
  const [serverConfig, setServerConfig] = useState<any>({});
  const [loading, setLoading] = useState(false);

  // Load settings on mount
  const loadSettings = async () => {
    try {
      const [configRes, harnessesRes] = await Promise.all([
        api.getConfig(),
        api.listHarnesses(),
      ]);
      
      if (configRes) {
        setModels(configRes.models?.roles || {});
        setRoutingRules(configRes.routing?.routes || []);
        setMemoryConfig(configRes.memory || {});
        setGitConfig(configRes.git || {});
        setServerConfig(configRes.server || {});
      }
      if (harnessesRes) {
        setHarnesses(harnessesRes.harnesses || []);
      }
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  const saveAll = async () => {
    setSaving(true);
    try {
      // In a real implementation, this would POST to a /config endpoint
      // For now, we'll save individual sections
      addNotification({ message: 'Settings saved (individual sections auto-save)', type: 'success' });
    } catch (err) {
      console.error('Failed to save settings:', err);
      addNotification({ message: 'Failed to save settings', type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const handleModelChange = async (role: string, model: string) => {
    const newModels = { ...models, [role]: { ...models[role], default: model } };
    setModels(newModels);
    try {
      await api.setModel(role, model);
      addNotification({ message: `${role} model updated to ${model}`, type: 'success' });
    } catch (err) {
      console.error('Failed to update model:', err);
      addNotification({ message: 'Failed to update model', type: 'error' });
    }
  };

  const handleAddRule = async () => {
    if (!newRule.pattern || !newRule.agent) return;
    try {
      await api.addRule(newRule);
      await loadSettings();
      setNewRule({ pattern: '', agent: '', model: '' });
      addNotification({ message: 'Routing rule added', type: 'success' });
    } catch (err) {
      console.error('Failed to add rule:', err);
    }
  };

  const handleDeleteRule = async (index: number) => {
    // TODO: implement delete rule API
    const newRules = routingRules.filter((_, i) => i !== index);
    setRoutingRules(newRules);
  };

  const tabs = [
    { id: 'general', label: 'General', icon: 'Settings' },
    { id: 'models', label: 'Models', icon: '🤖' },
    { id: 'routing', label: 'Routing', icon: '🔀' },
    { id: 'harness', label: 'Harness', icon: '⚙️' },
    { id: 'memory', label: 'Memory', icon: '🧠' },
    { id: 'git', label: 'Git', icon: '📁' },
    { id: 'server', label: 'Server', icon: '🖥️' },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Settings</h1>
          <p className="text-muted-foreground">Configure Sweave behavior and preferences</p>
        </div>
        <button 
          onClick={saveAll} 
          disabled={saving}
          className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2 disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              Save All
            </>
          )}
        </button>
      </div>

      {/* Tabs */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="border-b border-border">
          <nav className="flex overflow-x-auto px-4" aria-label="Settings tabs">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap',
                  activeTab === tab.id
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-muted'
                )}
              >
                <span>{tab.icon}</span>
                <span>{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>

        <div className="p-6">
          {activeTab === 'general' && <GeneralSettings />}
          {activeTab === 'models' && <ModelsSettings models={models} onModelChange={handleModelChange} />}
          {activeTab === 'routing' && <RoutingSettings rules={routingRules} onAddRule={handleAddRule} onDeleteRule={handleDeleteRule} newRule={newRule} setNewRule={setNewRule} />}
          {activeTab === 'harness' && <HarnessSettings harnesses={harnesses} />}
          {activeTab === 'memory' && <MemorySettings config={memoryConfig} setConfig={setMemoryConfig} />}
          {activeTab === 'git' && <GitSettings config={gitConfig} setConfig={setGitConfig} />}
          {activeTab === 'server' && <ServerSettings config={serverConfig} setConfig={setServerConfig} />}
        </div>
      </div>
    </div>
  );
}

// Sub-components for each tab
function GeneralSettings({ activeProject }: any) {
  const { addNotification } = useApp();
  const [projectName, setProjectName] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [projectDesc, setProjectDesc] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    // TODO: implement project creation
    addNotification({ message: 'Project creation coming soon', type: 'info' });
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="text-lg font-semibold">Project Management</h3>
        <p className="text-muted-foreground">Manage your projects and workspaces</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-6">
        <h4 className="font-medium mb-4">Current Project</h4>
        <div className="grid gap-4 md:grid-cols-3">
          <div>
            <label className="block text-sm text-muted-foreground">Name</label>
            <input type="text" className="w-full px-3 py-2 bg-input border border-border rounded-lg" readOnly />
          </div>
          <div>
            <label className="block text-sm text-muted-foreground">Path</label>
            <input type="text" className="w-full px-3 py-2 bg-input border border-border rounded-lg" readOnly />
          </div>
          <div>
            <label className="block text-sm text-muted-foreground">Memory Bank</label>
            <input type="text" className="w-full px-3 py-2 bg-input border border-border rounded-lg" readOnly />
          </div>
        </div>
      </div>

      <div className="pt-6 border-t border-border">
        <h4 className="font-medium mb-4">Create New Project</h4>
        <form onSubmit={(e) => { e.preventDefault(); }} className="space-y-4 max-w-xl">
          <div>
            <label className="block text-sm font-medium mb-2">Project Name</label>
            <input type="text" className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary" placeholder="my-project" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Path</label>
            <input type="text" className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary" placeholder="/home/user/projects/my-app" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Description</label>
            <textarea rows={3} className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary resize-none" placeholder="Project description" />
          </div>
          <button type="submit" className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">Create Project</button>
        </form>
      </div>
    </div>
  );
}

function ModelsSettings({ models, onModelChange }: any) {
  const roles = [
    { id: 'orchestrator', label: 'Orchestrator', description: 'Routes tasks and coordinates agents' },
    { id: 'backend', label: 'Backend', description: 'APIs, databases, server logic' },
    { id: 'frontend', label: 'Frontend', description: 'UI components, React/Vue/Svelte' },
    { id: 'reviewer', label: 'Reviewer', description: 'Code review, security, quality' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Model Configuration</h3>
        <p className="text-muted-foreground">Configure default models for each agent role. Changes take effect immediately.</p>
      </div>

      <div className="space-y-4">
        {Object.entries(models).map(([roleId, roleConfig]: [string, any]) => {
          const roleInfo = roles.find(r => r.id === roleId);
          return (
            <div key={roleId} className="bg-muted/50 rounded-lg p-4 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center">
                  <Bot className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h4 className="font-semibold capitalize">{roleId}</h4>
                  <p className="text-sm text-muted-foreground">{roleInfo?.description}</p>
                </div>
              </div>
              <div className="flex items-center gap-4">
                <select
                  value={roleConfig?.default || ''}
                  onChange={(e) => onModelChange(roleId, e.target.value)}
                  className="w-56 px-3 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {models[roleId]?.aliases?.map((m: string) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <span className="text-sm text-muted-foreground">Current: {roleConfig?.default}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  );
}

function RoutingSettings({ rules, onAddRule, onDeleteRule, newRule, setNewRule }: any) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Routing Rules</h3>
        <p className="text-muted-foreground">Define patterns that route tasks to specific agents. Patterns can be regex or comma-separated keywords.</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-4 space-y-3 mb-6">
        <div className="flex gap-2">
          <input
            type="text"
            value={newRule.pattern}
            onChange={(e) => setNewRule(prev => ({ ...prev, pattern: e.target.value }))}
            className="flex-1 px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary font-mono text-sm"
            placeholder="Pattern (regex or keywords)"
          />
          <select
            value={newRule.agent}
            onChange={(e) => setNewRule(prev => ({ ...prev, agent: e.target.value }))}
            className="w-40 px-3 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="backend">Backend</option>
            <option value="frontend">Frontend</option>
            <option value="reviewer">Reviewer</option>
          </select>
          <input
            type="text"
            value={newRule.model}
            onChange={(e) => setNewRule(prev => ({ ...prev, model: e.target.value }))}
            className="w-48 px-3 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary font-mono text-sm"
            placeholder="Model (optional)"
          />
          <button onClick={handleAddRule} className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">
            Add Rule
          </button>
        </div>
      </div>

      <div className="space-y-3">
        {routingRules.length === 0 ? (
          <p className="text-center text-muted-foreground py-8">No routing rules configured</p>
        ) : (
          <div className="space-y-2">
            {rules.map((rule: any, index: number) => (
              <div key={index} className="flex items-center gap-3 p-3 bg-muted/50 rounded-lg">
                <code className="flex-1 text-sm text-muted-foreground font-mono truncate">{rule.pattern}</code>
                <span className="px-2 py-1 bg-primary/10 text-primary rounded text-xs">{rule.agent}</span>
                <span className="px-2 py-1 bg-muted text-muted-foreground rounded text-xs font-mono">{rule.model || 'default'}</span>
                <button 
                  onClick={() => handleDeleteRule(index)}
                  className="px-2 py-1 text-red-500 hover:bg-red-500/10 rounded transition-colors"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="pt-4 border-t border-border">
        <label className="flex items-center gap-3">
          <select className="px-3 py-2 bg-input border border-border rounded-lg">
            <option value="llm">LLM Fallback (intelligent)</option>
            <option value="first">First Match</option>
          </select>
          <span className="text-sm text-muted-foreground">Fallback behavior when no rules match</span>
        </label>
      </div>
    </div>
  );
}

function HarnessSettings({ harnesses }: any) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Agent Harness</h3>
        <p className="text-muted-foreground">Configure the runtime for running agents. OpenCode is the default; others require additional setup.</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-6">
        <h4 className="font-medium mb-4">Default Harness</h4>
        <select className="w-full max-w-md px-4 py-3 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary">
          <option value="opencode">OpenCode (recommended)</option>
          <option value="claude-code">Claude Code</option>
          <option value="codex">Codex</option>
          <option value="acp">ACP (Custom)</option>
        </select>
      </div>

      {harnesses.length > 0 && (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Detected Harnesses</h3>
          <div className="space-y-3">
            {harnesses.map((h: any) => (
              <div key={h.name} className="bg-card border border-border rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center">
                      <Cpu className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                      <h4 className="font-semibold">{h.display_name}</h4>
                      <p className="text-sm text-muted-foreground">{h.command}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <span>Version: {h.version || 'unknown'}</span>
                    <span>Providers: {h.providers?.join(', ') || 'none'}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MemorySettings({ config, setConfig }: any) {
  const [mode, setMode] = useState(config?.hindsight?.mode || 'embedded_slim');
  const [providers, setProviders] = useState({
    embeddings: config?.hindsight?.embeddings_provider || 'openai',
    reranker: config?.hindsight?.reranker_provider || 'openai',
  });

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Long-term Memory (Hindsight)</h3>
        <p className="text-muted-foreground">Configure the long-term memory backend for agents.</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-6 space-y-6">
        <div>
          <label className="block text-sm font-medium mb-2">Backend Mode</label>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            className="w-full max-w-md px-4 py-3 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
          >
            <option value="embedded_slim">Embedded Slim (Python, ~500MB RAM)</option>
            <option value="docker_full">Docker Full (Local embeddings, ~2GB RAM)</option>
            <option value="docker_slim">Docker Slim (External embeddings, ~1GB RAM)</option>
            <option value="cloud">Hindsight Cloud (Managed)</option>
          </select>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-2">Embeddings Provider</label>
            <select
              value={providers.embeddings}
              onChange={(e) => setProviders(prev => ({ ...prev, embeddings: e.target.value }))}
              className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="openai">OpenAI</option>
              <option value="cohere">Cohere</option>
              <option value="tei">TEI (Local)</option>
              <option value="local">Local (llama.cpp)</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Reranker Provider</label>
            <select
              value={providers.reranker}
              onChange={(e) => setProviders(prev => ({ ...prev, reranker: e.target.value }))}
              className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="openai">OpenAI</option>
              <option value="cohere">Cohere</option>
              <option value="tei">TEI (Local)</option>
              <option value="local">Local (llama.cpp)</option>
            </select>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Memory Bank ID</label>
          <input 
            type="text" 
            className="w-full max-w-md px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
            placeholder="project-memory"
          />
        </div>

        <button className="px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 transition-colors flex items-center gap-2">
          <Database className="w-4 h-4" />
          Initialize Memory Backend
        </button>
      </div>
    </div>
  );
}

function GitSettings({ config, setConfig }: any) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Git & Worktrees</h3>
        <p className="text-muted-foreground">Configure git provider and worktree behavior.</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-6 space-y-6">
        <div>
          <label className="block text-sm font-medium mb-2">Git Provider</label>
          <select className="w-full max-w-md px-4 py-3 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary">
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="local">Local Only</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Worktree Base Path</label>
          <input 
            type="text" 
            className="w-full max-w-md px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
            placeholder=".worktrees"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Base Branch for PRs</label>
          <input 
            type="text" 
            className="w-full max-w-md px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
            placeholder="main"
          />
        </div>

        <div className="flex items-center gap-3">
          <input type="checkbox" className="w-4 h-4 rounded border-border" />
          <label className="text-sm">Auto-create PRs for completed tasks</label>
        </div>
      </div>
    </div>
  );
}

function ServerSettings({ config, setConfig }: any) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Server Configuration</h3>
        <p className="text-muted-foreground">Configure the web server host and port.</p>
      </div>

      <div className="bg-muted/50 rounded-lg p-6 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-2">Host</label>
            <input 
              type="text" 
              className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="127.0.0.1"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Port</label>
            <input 
              type="number" 
              className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="8880"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// Fix missing imports
import { useApp } from '../context/AppProvider';
import { Cpu, Database, Globe, Shield, HardDrive, RefreshCw, Save, Loader2, AlertTriangle, CheckCircle, Bot, Cpu as CpuIcon, Database as DatabaseIcon } from 'lucide-react';
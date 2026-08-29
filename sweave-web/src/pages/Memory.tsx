import { useApp } from '../context/AppProvider';
import { useState } from 'react';
import { Search, Zap, Brain, Plus, Trash2, Download, Upload, RefreshCw } from 'lucide-react';
import { cn } from '../utils/cn';
import { api } from '../api/client';

export function Memory() {
  const { activeProject, activeSession, addNotification } = useApp();
  const [activeTab, setActiveTab] = useState<'recall' | 'reflect' | 'retain' | 'browser'>('recall');
  
  // Recall
  const [recallQuery, setRecallQuery] = useState('');
  const [recallLimit, setRecallLimit] = useState(10);
  const [recallScope, setRecallScope] = useState<'global' | 'project' | 'session'>('project');
  const [recallResults, setRecallResults] = useState<any[]>([]);
  const [recallLoading, setRecallLoading] = useState(false);

  // Reflect
  const [reflectQuery, setReflectQuery] = useState('');
  const [reflectScope, setReflectScope] = useState<'global' | 'project' | 'session'>('project');
  const [reflectResult, setReflectResult] = useState('');
  const [reflectLoading, setReflectLoading] = useState(false);

  // Retain
  const [retainContent, setRetainContent] = useState('');
  const [retainTags, setRetainTags] = useState('');
  const [retainScope, setRetainScope] = useState<'global' | 'project' | 'session'>('project');
  const [retainLoading, setRetainLoading] = useState(false);
  const [retainResult, setRetainResult] = useState('');

  // Browser
  const [browserQuery, setBrowserQuery] = useState('');
  const [browserScope, setBrowserScope] = useState<'global' | 'project' | 'session'>('project');
  const [browserResults, setBrowserResults] = useState<any[]>([]);
  const [browserLoading, setBrowserLoading] = useState(false);

  const handleRecall = async () => {
    if (!recallQuery.trim()) return;
    setRecallLoading(true);
    try {
      const res = await api.recallMemory(recallQuery, undefined, recallLimit);
      setRecallResults(res.memories || []);
      addNotification({ message: `Found ${res.memories?.length || 0} memories`, type: 'success' });
    } catch (err) {
      console.error('Recall failed:', err);
      addNotification({ message: 'Recall failed', type: 'error' });
    } finally {
      setRecallLoading(false);
    }
  };

  const handleReflect = async () => {
    if (!reflectQuery.trim()) return;
    setReflectLoading(true);
    try {
      const res = await api.reflectMemory(reflectQuery);
      setReflectResult(res.reflection);
      addNotification({ message: 'Reflection complete', type: 'success' });
    } catch (err) {
      console.error('Reflect failed:', err);
      addNotification({ message: 'Reflect failed', type: 'error' });
    } finally {
      setReflectLoading(false);
    }
  };

  const handleRetain = async () => {
    if (!retainContent.trim()) return;
    setRetainLoading(true);
    try {
      const tags = retainTags.split(',').map(t => t.trim()).filter(Boolean);
      const res = await api.retainMemory(retainContent, undefined, retainTags.split(',').map(t => t.trim()).filter(Boolean));
      setRetainResult(res.result);
      setRetainContent('');
      setRetainTags('');
      addNotification({ message: 'Memory retained', type: 'success' });
    } catch (err) {
      console.error('Retain failed:', err);
      addNotification({ message: 'Retain failed', type: 'error' });
    } finally {
      setRetainLoading(false);
    }
  };

  const handleBrowserSearch = async () => {
    if (!browserQuery.trim()) return;
    setBrowserLoading(true);
    try {
      const res = await api.recallMemory(browserQuery, undefined, 50);
      setBrowserResults(res.memories || []);
    } catch (err) {
      console.error('Search failed:', err);
    } finally {
      setBrowserLoading(false);
    }
  };

  const tabs = [
    { id: 'recall', label: 'Recall', icon: Search },
    { id: 'reflect', label: 'Reflect', icon: Zap },
    { id: 'retain', label: 'Retain', icon: Brain },
    { id: 'browser', label: 'Browse', icon: Brain },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Long-term Memory</h1>
          <p className="text-muted-foreground">Store, search, and reflect on memories across projects and sessions</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-sm flex items-center gap-2">
            <Download className="w-4 h-4" />
            Export
          </button>
          <button className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-lg text-sm flex items-center gap-2">
            <Upload className="w-4 h-4" />
            Import
          </button>
          <button className="px-3 py-1.5 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 flex items-center gap-2">
            <RefreshCw className="w-4 h-4" />
            Sync
          </button>
        </div>
      </div>

      {/* Memory Bank Info */}
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-center">
          <div className="p-4 bg-muted/50 rounded-lg">
            <p className="text-sm text-muted-foreground">Global</p>
            <p className="text-2xl font-bold">global</p>
            <p className="text-xs text-muted-foreground">Shared across all projects</p>
          </div>
          <div className="p-4 bg-muted/50 rounded-lg">
            <p className="text-sm text-muted-foreground">Project</p>
            <p className="text-2xl font-bold">project-{activeProject?.name || 'none'}</p>
            <p className="text-xs text-muted-foreground">Isolated per project</p>
          </div>
          <div className="p-4 bg-muted/50 rounded-lg">
            <p className="text-sm text-muted-foreground">Session</p>
            <p className="text-2xl font-bold truncate">{activeSession?.memory_bank || 'none'}</p>
            <p className="text-xs text-muted-foreground">Isolated per session</p>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="border-b border-border">
          <nav className="flex overflow-x-auto px-4" aria-label="Memory tabs">
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
                <tab.icon className="w-4 h-4" />
                <span>{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>

        <div className="p-6">
          {activeTab === 'recall' && (
            <div className="space-y-6 max-w-3xl">
              <div>
                <h3 className="text-lg font-semibold mb-4">Recall Memories</h3>
                <p className="text-muted-foreground mb-4">Search for relevant memories across your memory banks</p>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Search Query</label>
                  <textarea
                    value={recallQuery}
                    onChange={(e) => setRecallQuery(e.target.value)}
                    rows={3}
                    className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                    placeholder="What are you looking for? e.g., 'authentication decisions', 'API design patterns'"
                  />
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium mb-2">Scope</label>
                    <select
                      value={recallScope}
                      onChange={(e) => setRecallScope(e.target.value as any)}
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      <option value="global">Global (all projects)</option>
                      <option value="project">Project only</option>
                      <option value="session">Session only</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-2">Limit</label>
                    <input
                      type="number"
                      value={recallLimit}
                      onChange={(e) => setRecallLimit(Number(e.target.value))}
                      min="1"
                      max="100"
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-2">&nbsp;</label>
                    <button
                      onClick={handleRecall}
                      disabled={recallLoading || !recallQuery.trim()}
                      className="w-full px-4 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {recallLoading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Searching...
                        </>
                      ) : (
                        <>
                          <Search className="w-4 h-4" />
                          Recall Memories
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>

              {recallResults.length > 0 && (
                <div className="space-y-3">
                  <h4 className="font-medium">Results ({recallResults.length})</h4>
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {recallResults.map((mem: any, idx: number) => (
                      <div key={idx} className="bg-muted/50 rounded-lg p-4 border border-border">
                        <p className="font-mono text-sm text-muted-foreground whitespace-pre-wrap">{mem.content}</p>
                        <div className="flex items-center gap-2 mt-2 text-xs">
                          {mem.tags?.map((tag: string) => (
                            <span key={tag} className="px-2 py-0.5 bg-muted rounded text-xs">{tag}</span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'reflect' && (
            <div className="space-y-6 max-w-3xl">
              <div>
                <h3 className="text-lg font-semibold mb-4">Reflect on Memories</h3>
                <p className="text-muted-foreground mb-4">Synthesize insights from your memories. Ask questions like 'What did we decide about authentication?'</p>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Reflection Query</label>
                  <textarea
                    value={reflectQuery}
                    onChange={(e) => setReflectQuery(e.target.value)}
                    rows={3}
                    className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                    placeholder="What do you want to understand? e.g., 'Summarize our authentication architecture decisions'"
                  />
                </div>
                
                <div className="flex gap-4">
                  <div className="flex-1">
                    <label className="block text-sm font-medium mb-2">Scope</label>
                    <select
                      value={reflectScope}
                      onChange={(e) => setReflectScope(e.target.value as any)}
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      <option value="global">Global (all projects)</option>
                      <option value="project">Project only</option>
                      <option value="session">Session only</option>
                    </select>
                  </div>
                  <div className="flex-1">
                    <label className="block text-sm font-medium mb-2">&nbsp;</label>
                    <button
                      onClick={handleReflect}
                      disabled={reflectLoading || !reflectQuery.trim()}
                      className="w-full px-4 py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {reflectLoading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Reflecting...
                        </>
                      ) : (
                        <>
                          <Zap className="w-4 h-4" />
                          Reflect
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>

              {reflectResult && (
                <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="font-medium text-amber-500">Reflection Result</h4>
                    <button onClick={() => setReflectResult('')} className="text-muted-foreground hover:text-foreground">Clear</button>
                  </div>
                  <pre className="whitespace-pre-wrap font-mono text-sm">{reflectResult}</pre>
                </div>
              )}
            </div>
          )}

          {activeTab === 'retain' && (
            <div className="space-y-6 max-w-3xl">
              <div>
                <h3 className="text-lg font-semibold mb-4">Retain Memory</h3>
                <p className="text-muted-foreground mb-4">Store a decision, fact, or preference for future reference</p>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Content to Remember</label>
                  <textarea
                    value={retainContent}
                    onChange={(e) => setRetainContent(e.target.value)}
                    rows={4}
                    className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                    placeholder="What should be remembered? e.g., 'We decided to use JWT for authentication with 15min expiry'"
                  />
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium mb-2">Scope</label>
                    <select
                      value={retainScope}
                      onChange={(e) => setRetainScope(e.target.value as any)}
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      <option value="global">Global (all projects)</option>
                      <option value="project">Project only</option>
                      <option value="session">Session only</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-2">Tags (comma-separated)</label>
                    <input
                      type="text"
                      value={retainTags}
                      onChange={(e) => setRetainTags(e.target.value)}
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                      placeholder="auth, jwt, architecture"
                    />
                  </div>
                </div>
                
                <button
                  onClick={handleRetain}
                  disabled={retainLoading || !retainContent.trim()}
                  className="w-full max-w-md px-4 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {retainLoading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Storing...
                    </>
                  ) : (
                    <>
                      <Brain className="w-4 h-4" />
                      Retain Memory
                    </>
                  )}
                </button>

                {retainResult && (
                  <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-4">
                    <p className="text-green-500 font-medium mb-2">Stored Successfully</p>
                    <p className="text-sm">{retainResult}</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'browser' && (
            <div className="space-y-6">
              <div>
                <h3 className="text-lg font-semibold mb-4">Browse Memories</h3>
                <p className="text-muted-foreground mb-4">Search and explore all stored memories</p>
              </div>
              
              <div className="space-y-4">
                <div className="flex gap-4">
                  <div className="flex-1">
                    <label className="block text-sm font-medium mb-2">Search</label>
                    <input
                      type="text"
                      value={browserQuery}
                      onChange={(e) => setBrowserQuery(e.target.value)}
                      className="w-full bg-input border border-border rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary"
                      placeholder="Search memories..."
                    />
                  </div>
                  <div className="flex gap-2">
                    <select
                      value={browserScope}
                      onChange={(e) => setBrowserScope(e.target.value as any)}
                      className="px-4 py-3 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                    >
                      <option value="global">Global</option>
                      <option value="project">Project</option>
                      <option value="session">Session</option>
                    </select>
                    <button
                      onClick={handleBrowserSearch}
                      disabled={browserLoading || !browserQuery.trim()}
                      className="px-4 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
                    >
                      {browserLoading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Search
                        </>
                      ) : (
                        <>
                          <Search className="w-4 h-4" />
                          Search
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>

              {browserResults.length > 0 && (
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {browserResults.map((mem: any, idx: number) => (
                    <div key={idx} className="bg-muted/50 rounded-lg p-4 border border-border">
                      <p className="font-mono text-sm text-muted-foreground whitespace-pre-wrap">{mem.content}</p>
                      <div className="flex items-center gap-2 mt-2 text-xs">
                        {mem.tags?.map((tag: string) => (
                          <span key={tag} className="px-2 py-0.5 bg-muted rounded text-xs">{tag}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
import { useApp } from '../context/AppProvider';
import { Plus, FolderGit2, Edit, Trash2, Check, ChevronRight, FolderOpen } from 'lucide-react';
import { useState } from 'react';
import { cn } from '../utils/cn';
import { useApp } from '../context/AppProvider';

export function Projects() {
  const { projects, activeProject, setActiveProject, createProject, deleteProject, setActiveView, addNotification } = useApp();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingProject, setEditingProject] = useState<any>(null);
  const [formData, setFormData] = useState({ name: '', path: '', description: '' });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.path) return;

    try {
      if (editingProject) {
        // TODO: implement update
        addNotification({ message: 'Project updated', type: 'success' });
      } else {
        await createProject(formData);
        addNotification({ message: 'Project created', type: 'success' });
      }
      setShowCreateModal(false);
      setEditingProject(null);
      setFormData({ name: '', path: '', description: '' });
    } catch (err) {
      console.error('Failed to save project:', err);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete project "${name}"? This cannot be undone.`)) return;
    try {
      await deleteProject(name);
      addNotification({ message: 'Project deleted', type: 'success' });
    } catch (err) {
      console.error('Failed to delete project:', err);
    }
  };

  const handleSetActive = (name: string) => {
    setActiveProject(name);
  };

  const openCreateModal = () => {
    setEditingProject(null);
    setFormData({ name: '', path: '', description: '' });
    setShowCreateModal(true);
  };

  const openEditModal = (project: any) => {
    setEditingProject(project);
    setFormData({ name: project.name, path: project.path, description: project.description });
    setShowCreateModal(true);
  };

  const renderProjectCard = (project: any) => (
    <div
      key={project.name}
      className={cn(
        'bg-card border border-border rounded-xl p-6 transition-all hover:shadow-lg',
        activeProject?.name === project.name && 'border-primary/50 ring-1 ring-primary/20'
      )}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center">
            <FolderOpen className="w-6 h-6 text-primary" />
          </div>
          <div>
            <h3 className="font-semibold">{project.name}</h3>
            <p className="text-sm text-muted-foreground truncate max-w-xs">{project.path}</p>
          </div>
        </div>
        {activeProject?.name === project.name && (
          <div className="flex items-center gap-2 px-2 py-1 bg-primary/10 text-primary rounded-full text-xs font-medium">
            <Check className="w-3 h-3" />
            Active
          </div>
        )}
      </div>

      {project.description && (
        <p className="text-sm text-muted-foreground mb-4 line-clamp-2">{project.description}</p>
      )}

      <div className="flex items-center justify-between pt-4 border-t border-border">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <FolderOpen className="w-3 h-3" />
          <span>{new Date(project.updated_at).toLocaleDateString()}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => handleSetActive(project.name)}
            className={cn(
              'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1',
              activeProject?.name === project.name
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground'
            )}
            disabled={activeProject?.name === project.name}
          >
            {activeProject?.name === project.name ? (
              <>
                <Check className="w-3 h-3" />
                Active
              </>
            ) : (
              <>
                <ChevronRight className="w-3 h-3" />
                Activate
              </>
            )}
          </button>
          <button
            onClick={() => openEditModal(project)}
            className="px-3 py-1.5 rounded-lg text-sm hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          >
            Edit
          </button>
          <button
            onClick={() => handleDelete(project.name)}
            className="px-3 py-1.5 rounded-lg text-sm hover:bg-red-500/10 hover:text-red-500 transition-colors text-muted-foreground"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );

  const projectCards = projects.map(renderProjectCard);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Projects</h1>
          <p className="text-muted-foreground">Manage your projects and workspaces</p>
        </div>
        <button
          onClick={() => { setEditingProject(null); setFormData({ name: '', path: '', description: '' }); setShowCreateModal(true); }}
          className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          New Project
        </button>
      </div>

      {projects.length === 0 ? (
        <div className="bg-card border border-border rounded-lg p-12 text-center">
          <FolderGit2 className="w-16 h-16 mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold mb-2">No projects yet</h2>
          <p className="text-muted-foreground mb-6">Create your first project to start organizing your work</p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2 mx-auto"
          >
            <Plus className="w-4 h-4" />
            Create Project
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {projects.length > 0 ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {projects.map(renderProjectCard)}
            </div>
          ) : (
            <div>No projects</div>
          )}
        </div>
      )}

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-card rounded-xl border border-border w-full max-w-md mx-4 max-h-[90vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <h3 className="text-lg font-semibold">{editingProject ? 'Edit Project' : 'Create Project'}</h3>
              <button onClick={() => { setShowCreateModal(false); setEditingProject(null); }} className="text-muted-foreground hover:text-foreground">
                &times;
              </button>
            </div>
            <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto">
              <div>
                <label className="block text-sm font-medium mb-2">Name *</label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="my-project"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Path *</label>
                <input
                  type="text"
                  value={formData.path}
                  onChange={(e) => setFormData(prev => ({ ...prev, path: e.target.value }))}
                  className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder="/home/user/projects/my-app"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Description</label>
                <textarea
                  value={formData.description}
                  onChange={(e) => setFormData(prev => ({ ...prev, description: e.target.value }))}
                  rows={3}
                  className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                  placeholder="Project description (optional)"
                />
              </div>
              <div className="flex justify-end gap-3 pt-4 border-t border-border">
                <button type="button" onClick={() => { setShowCreateModal(false); setEditingProject(null); }} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg">
                  Cancel
                </button>
                <button type="submit" className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">
                  {editingProject ? 'Save Changes' : 'Create Project'}
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
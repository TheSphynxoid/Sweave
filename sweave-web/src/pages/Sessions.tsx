import { useApp } from '../context/AppProvider';
import { Plus, MessageSquare, Play, Trash2, Edit, MessageSquare as MsgIcon, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { cn } from '../utils/cn';
import React from 'react';

export function Sessions() {
  const { 
    sessions, 
    activeProject, 
    activeSession, 
    createSession, 
    setActiveSession, 
    setActiveView,
    fetchSessions,
    addNotification 
  } = useApp();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [formData, setFormData] = useState({ name: '' });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) return;

    try {
      await createSession({ 
        name: formData.name.trim(), 
        project_name: activeProject?.name 
      });
      addNotification({ message: 'Session created', type: 'success' });
      setShowCreateModal(false);
      setFormData({ name: '' });
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  };

  const handleDelete = async (sessionId: string) => {
    if (!confirm('Delete this session?')) return;
    try {
      // TODO: implement delete session API
      addNotification({ message: 'Session deleted', type: 'success' });
      await fetchSessions(activeProject?.name);
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  };

  const handleSetActive = (sessionId: string) => {
    setActiveSession(sessionId);
    setActiveView('tasks');
  };

  const handleCreate = () => {
    setFormData({ name: '' });
    setShowCreateModal(true);
  };

  const renderSessionsList = () => (
    <div className="space-y-3">
      {sessions.map((session) => (
        <div
          key={session.id}
          className={cn(
            'bg-card border border-border rounded-xl p-4 transition-all hover:shadow-lg flex items-center justify-between',
            activeSession?.id === session.id && 'border-primary/50 ring-1 ring-primary/20'
          )}
        >
          <div className="flex items-center gap-4 flex-1 min-w-0">
            <div className="w-12 h-12 bg-green-500/10 rounded-lg flex items-center justify-center flex-shrink-0">
              <MessageSquare className="w-6 h-6 text-green-500" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-3">
                <h3 className="font-semibold truncate">{session.name}</h3>
                {activeSession?.id === session.id && (
                  <span className="px-2 py-0.5 bg-green-500/10 text-green-500 rounded-full text-xs font-medium">Active</span>
                )}
              </div>
              <p className="text-sm text-muted-foreground truncate">
                Created {new Date(session.created_at).toLocaleString()}
              </p>
            </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleSetActive(session.id)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1',
                  activeSession?.id === session.id
                    ? 'bg-green-500/10 text-green-500'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                )}
                disabled={activeSession?.id === session.id}
              >
                {activeSession?.id === session.id ? (
                  <>
                    <span className="w-3 h-3 bg-green-500 rounded-full" />
                    Active
                  </>
                ) : (
                  <>
                    <Play className="w-3 h-3" />
                    Open
                  </>
                )}
              </button>
              <button
                onClick={() => handleDelete(session.id)}
                className="px-3 py-1.5 rounded-lg hover:bg-red-500/10 hover:text-red-500 transition-colors text-muted-foreground"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))
      </div>
    );

  const renderCreateModal = () => (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateModal(false)}>
      <div className="bg-card rounded-xl border border-border w-full max-w-md mx-4 max-h-[90vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-lg font-semibold">Create Session</h3>
          <button onClick={() => setShowCreateModal(false)} className="text-muted-foreground hover:text-foreground">&times;</button>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-2">Session Name *</label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
              className="w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="feature-auth, bug-fix-login, etc."
              required
              autoFocus
            />
          </div>
          <div className="flex justify-end gap-3 pt-4 border-t border-border">
            <button type="button" onClick={() => setShowCreateModal(false)} className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg">
              Cancel
            </button>
            <button type="submit" className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">
              Create Session
            </button>
          </div>
        </form>
      </div>
    </div>
  );

  if (!activeProject) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Sessions</h1>
            <p className="text-muted-foreground">Select a project to manage sessions</p>
          </div>
        </div>
        <div className="bg-card border border-border rounded-lg p-12 text-center">
          <MessageSquare className="w-16 h-16 mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold mb-2">No project selected</h2>
          <p className="text-muted-foreground mb-6">Select or create a project to start managing sessions</p>
          <button 
            className="px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
            onClick={() => setActiveView('projects')}
          >
            Go to Projects
          </button>
        </div>
      </div>
    );
  }

  return React.createElement(
    'div',
    { className: 'space-y-6' },
    React.createElement(
      'div',
      { className: 'flex items-center justify-between' },
      React.createElement(
        'div',
        null,
        React.createElement('h1', { className: 'text-2xl font-bold' }, 'Sessions'),
        React.createElement('p', { className: 'text-muted-foreground' }, `Manage sessions for ${activeProject?.name}`)
      ),
      React.createElement(
        'button',
        {
          onClick: handleCreate,
          className: 'px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2'
        },
        React.createElement(Plus, { className: 'w-4 h-4' }),
        'New Session'
      )
    ),
    sessions.length === 0 ? (
      React.createElement(
        'div',
        { className: 'bg-card border border-border rounded-lg p-12 text-center' },
        React.createElement(MessageSquare, { className: 'w-16 h-16 mx-auto mb-4 opacity-50' }),
        React.createElement('h2', { className: 'text-xl font-semibold mb-2' }, 'No sessions yet'),
        React.createElement('p', { className: 'text-muted-foreground mb-6' }, 'Create your first session to start working'),
        React.createElement(
          'button',
          {
            onClick: handleCreate,
            className: 'px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2 mx-auto'
          },
          React.createElement(Plus, { className: 'w-4 h-4' }),
          'Create Session'
        )
      ) : (
        React.createElement(
          'div',
          { className: 'space-y-3' },
          sessions.map((session) => React.createElement(
            'div',
            {
              key: session.id,
              className: cn(
                'bg-card border border-border rounded-xl p-4 transition-all hover:shadow-lg flex items-center justify-between',
                activeSession?.id === session.id && 'border-primary/50 ring-1 ring-primary/20'
              )
            },
            React.createElement(
              'div',
              { className: 'flex items-center gap-4 flex-1 min-w-0' },
              React.createElement(
                'div',
                { className: 'w-12 h-12 bg-green-500/10 rounded-lg flex items-center justify-center flex-shrink-0' },
                React.createElement(MessageSquare, { className: 'w-6 h-6 text-green-500' })
              ),
              React.createElement(
                'div',
                { className: 'min-w-0' },
                React.createElement(
                  'div',
                  { className: 'flex items-center gap-3' },
                  React.createElement('h3', { className: 'font-semibold truncate' }, session.name),
                  activeSession?.id === session.id && React.createElement(
                    'span',
                    { className: 'px-2 py-0.5 bg-green-500/10 text-green-500 rounded-full text-xs font-medium' },
                    'Active'
                  )
                ),
                React.createElement(
                  'p',
                  { className: 'text-sm text-muted-foreground truncate' },
                  `Created ${new Date(session.created_at).toLocaleString()}`
                )
              )
            ),
            React.createElement(
              'div',
              { className: 'flex items-center gap-2' },
              React.createElement(
                'button',
                {
                  onClick: () => handleSetActive(session.id),
                  className: cn(
                    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1',
                    activeSession?.id === session.id
                      ? 'bg-green-500/10 text-green-500'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  ),
                  disabled: activeSession?.id === session.id
                },
                activeSession?.id === session.id ? (
                  React.createElement(
                    React.Fragment,
                    null,
                    React.createElement('span', { className: 'w-3 h-3 bg-green-500 rounded-full' }),
                    'Active'
                  )
                ) : (
                  React.createElement(
                    React.Fragment,
                    null,
                    React.createElement(Play, { className: 'w-3 h-3' }),
                    'Open'
                  )
                )
              ),
              React.createElement(
                'button',
                {
                  onClick: () => handleDelete(session.id),
                  className: 'px-3 py-1.5 rounded-lg hover:bg-red-500/10 hover:text-red-500 transition-colors text-muted-foreground'
                },
                React.createElement(Trash2, { className: 'w-4 h-4' })
              )
            )
          ))
        )
      ) : null)}
      {showCreateModal && React.createElement(
        'div',
        { className: 'fixed inset-0 z-50 flex items-center justify-center bg-black/50', onClick: () => setShowCreateModal(false) },
        React.createElement(
          'div',
          {
            className: 'bg-card rounded-xl border border-border w-full max-w-md mx-4 max-h-[90vh] overflow-hidden flex flex-col',
            onClick: (e: React.MouseEvent<HTMLDivElement>) => e.stopPropagation()
          },
          React.createElement(
            'div',
            { className: 'px-6 py-4 border-b border-border flex items-center justify-between' },
            React.createElement('h3', { className: 'text-lg font-semibold' }, 'Create Session'),
            React.createElement(
              'button',
              { onClick: () => setShowCreateModal(false), className: 'text-muted-foreground hover:text-foreground' },
              '&times;'
            )
          ),
          React.createElement(
            'form',
            { onSubmit: handleSubmit, className: 'p-6 space-y-4' },
            React.createElement(
              'div',
              null,
              React.createElement(
                'label',
                { className: 'block text-sm font-medium mb-2' },
                'Session Name *'
              ),
              React.createElement(
                'input',
                {
                  type: 'text',
                  value: formData.name,
                  onChange: (e: React.ChangeEvent<HTMLInputElement>) => setFormData(prev => ({ ...prev, name: e.target.value })),
                  className: 'w-full px-4 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary',
                  placeholder: 'feature-auth, bug-fix-login, etc.',
                  required: true,
                  autoFocus: true
                }
              )
            ),
            React.createElement(
              'div',
              { className: 'flex justify-end gap-3 pt-4 border-t border-border' },
              React.createElement(
                'button',
                {
                  type: 'button',
                  onClick: () => setShowCreateModal(false),
                  className: 'px-4 py-2 bg-muted hover:bg-muted/80 rounded-lg'
                },
                'Cancel'
              ),
              React.createElement(
                'button',
                { type: 'submit', className: 'px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90' },
                'Create Session'
              )
            )
          )
        )
      )
    )
  );
}
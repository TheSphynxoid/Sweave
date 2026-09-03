// Sweave - Clean Working JavaScript
// Sidebar navigation MUST work - no timing issues

(function() {
'use strict';

// ============================================
// State
// ============================================
const state = {
  project: null,
  session: null,
  currentTab: 'chat',
  fbPath: null,
  fbSelected: null,
  config: {},
  models: {},
  rules: { routes: [], fallback: 'llm' },
  memoryBanks: [],
  currentMemoryBank: null,
  children: [],
  ws: null,
};

// ============================================
// Utilities
// ============================================
function $(id) { return document.getElementById(id); }
function $$(sel) { return Array.from(document.querySelectorAll(sel)); }

function showError(msg) {
  const box = $('error-box');
  if (!box) return;
  box.textContent = msg;
  box.classList.remove('hidden');
  console.error(msg);
}

function toast(msg, type = 'info', duration = 4000) {
  const container = $('toasts');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = '<span>' + escapeHtml(msg) + '</span><button class="x">×</button>';
  el.querySelector('.x').onclick = () => el.remove();
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function escapeHtml(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

async function api(path, options = {}) {
  const res = await fetch('/api' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let err;
    try { err = await res.json(); } catch (e) { err = {}; }
    throw new Error(err.detail || ('HTTP ' + res.status));
  }
  return res.json();
}

// ============================================
// Global error handlers
// ============================================
window.addEventListener('error', (e) => {
  showError('JS Error: ' + (e.error?.message || e.message) + '\n' + (e.error?.stack || ''));
});

window.addEventListener('unhandledrejection', (e) => {
  showError('Promise Error: ' + (e.reason?.message || e.reason));
});

// ============================================
// Tab Navigation - CORE FUNCTIONALITY
// ============================================
function switchTab(tab) {
  console.log('[sweave] switchTab:', tab);

  // Welcome mode (no active project): only Settings is reachable
  if (!state.project && tab !== 'settings') {
    toast('Open a project first', 'warning');
    showWelcome();
    return;
  }
  if (!state.project && tab === 'settings') {
    $('welcome').classList.add('hidden');
    $('tab-settings').classList.remove('hidden');
  }

  state.currentTab = tab;

  // Update nav items
  $$('.nav-item').forEach(item => {
    if (item.dataset.tab === tab) item.classList.add('active');
    else item.classList.remove('active');
  });

  // Update tab panels
  const panels = {
    'chat': 'tab-chat',
    'children': 'tab-children',
    'agents': 'tab-agents',
    'memory': 'tab-memory',
    'settings': 'tab-settings',
  };
  Object.entries(panels).forEach(([key, id]) => {
    const panel = $(id);
    if (panel) {
      if (key === tab) panel.classList.add('active');
      else panel.classList.remove('active');
    }
  });

  // Load tab content if needed
  if (tab === 'agents' && state.project) loadAgents();
  if (tab === 'memory' && state.project) loadMemoryBanks();
  if (tab === 'settings') loadSettings();
}

function switchSettingsTab(name) {
  $$('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.stab === name));
  $$('.settings-pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
}

function switchMemoryTab(name) {
  $$('#m-tab-recall, #m-tab-reflect, #m-tab-retain').forEach(b => b.classList.remove('btn-primary'));
  $$('#m-tab-recall, #m-tab-reflect, #m-tab-retain').forEach(b => b.classList.add('btn-secondary'));
  if (name === 'recall') {
    $('m-tab-recall').classList.remove('btn-secondary');
    $('m-tab-recall').classList.add('btn-primary');
  } else if (name === 'reflect') {
    $('m-tab-reflect').classList.remove('btn-secondary');
    $('m-tab-reflect').classList.add('btn-primary');
  } else {
    $('m-tab-retain').classList.remove('btn-secondary');
    $('m-tab-retain').classList.add('btn-primary');
  }
  $$('.memory-pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
}

// ============================================
// App state
// ============================================
function showProjectMode() {
  $('welcome').classList.add('hidden');
  $$('.tab').forEach(t => t.classList.remove('hidden'));
  $('app').classList.remove('welcome-mode');
  updateTopbar();
  switchTab('chat');
}

function showWelcome() {
  $('welcome').classList.remove('hidden');
  $$('.tab').forEach(t => t.classList.add('hidden'));
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  $('app').classList.add('welcome-mode');
  updateTopbar();
  loadRecentProjects();
}

function updateTopbar() {
  $('project-link').textContent = state.project ? state.project.name : 'No project';
  $('session-link').textContent = state.session ? state.session.name : 'No session';
}

async function loadContext() {
  try {
    const project = await api('/projects/active');
    if (project && project.name) {
      state.project = project;
      try {
        const session = await api('/sessions/active');
        if (session && session.id) {
          state.session = session;
          await loadSessionData();
        } else {
          await loadProjectData();
        }
      } catch (e) {
        console.warn('No active session:', e);
        await loadProjectData();
      }
      showProjectMode();
    } else {
      showWelcome();
    }
  } catch (e) {
    console.error('Load context:', e);
    showWelcome();
  }
}

async function loadSessionData() {
  if (!state.session) return;
  try {
    const session = await api('/sessions/' + state.session.id);
    state.session = session;
    state.children = session.children || [];
    renderSessionMessages(session);
    renderChildren();
  } catch (e) {
    console.error('Load session:', e);
  }
  await loadProjectData();
}

async function loadProjectData() {
  if (!state.project) return;
  try {
    const config = await api('/config');
    state.config = config;
  } catch (e) { console.error('Load config:', e); }
  updateTopbar();
}

async function loadRecentProjects() {
  try {
    const res = await api('/projects');
    const projects = res.projects || [];
    const container = $('recent-list');
    container.innerHTML = '';
    if (projects.length === 0) {
      container.innerHTML = '<p style="color:var(--muted);text-align:center;padding:20px">No previous projects</p>';
      return;
    }
    projects.slice(0, 5).forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'recent-item';
      btn.innerHTML = '<span>' + escapeHtml(p.name) + '</span><span class="path">' + escapeHtml(p.path) + '</span>';
      btn.onclick = () => openProject(p.name);
      container.appendChild(btn);
    });
  } catch (e) { console.error('Load recent:', e); }
}

// ============================================
// Projects
// ============================================
async function openProject(name) {
  try {
    await api('/projects/' + encodeURIComponent(name) + '/active', { method: 'POST' });
    const res = await api('/projects');
    state.project = (res.projects || []).find(p => p.name === name) || { name };
    state.session = null;
    // Get or create a session
    const sessionsRes = await api('/sessions?project_name=' + encodeURIComponent(name));
    const sessions = sessionsRes.sessions || [];
    if (sessions.length > 0) {
      await switchSession(sessions[0].id);
    } else {
      await createNewSession();
    }
    showProjectMode();
    toast('Opened project: ' + name, 'success');
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function switchSession(id) {
  try {
    await api('/sessions/' + encodeURIComponent(id) + '/active', { method: 'POST' });
    await loadSessionData();
    updateTopbar();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function createNewSession() {
  if (!state.project) {
    toast('No project open', 'error');
    return;
  }
  try {
    const result = await api('/sessions', {
      method: 'POST',
      body: { name: 'Session ' + new Date().toLocaleString(), project_name: state.project.name }
    });
    if (result.success) {
      state.session = result.session;
      await loadSessionData();
      updateTopbar();
      switchTab('chat');
      toast('Session created', 'success');
    }
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ============================================
// Rendering
// ============================================
function renderSessionMessages(session) {
  const container = $('chat-messages');
  container.innerHTML = '';
  if (!session.messages || session.messages.length === 0) {
    container.innerHTML = '<div class="empty">Start a conversation.</div>';
    return;
  }
  session.messages.forEach(msg => container.appendChild(renderMessage(msg)));
  container.scrollTop = container.scrollHeight;
}

function renderMessage(msg) {
  const div = document.createElement('div');
  div.className = 'message ' + msg.role;
  const avatar = msg.role === 'user' ? 'U' : msg.role === 'tool' ? 'T' : 'A';
  div.innerHTML =
    '<div class="avatar">' + avatar + '</div>' +
    '<div class="content">' +
      '<div class="role">' + escapeHtml(msg.role) + '</div>' +
      '<div class="text">' + escapeHtml(msg.content) + '</div>' +
    '</div>';
  return div;
}

function renderChildren() {
  const list = $('children-list');
  list.innerHTML = '';
  if (state.children.length === 0) {
    list.innerHTML = '<div class="empty">No child sessions yet.</div>';
    updateChildrenBadge();
    return;
  }
  state.children.forEach(child => {
    const div = document.createElement('div');
    div.className = 'list-item ' + child.status;
    // Promote button (M1.4+M1.5 step 3): visible only for bridged
    // children in 'review'. The button calls POST
    // /api/delegations/{id}/promote, which transitions the
    // delegation to 'done' and mirrors the status back to the child.
    // R2's /cross-review will call the same endpoint programmatically.
    const showPromote = child.status === 'review' && child.delegation_id;
    const promoteBtn = showPromote
      ? '<button class="child-promote-btn" data-delegation-id="'
          + escapeHtml(child.delegation_id)
          + '">Mark done</button>'
      : '';
    div.innerHTML =
      '<div class="top"><span class="name">' + escapeHtml(child.agent_name) + '</span><span class="status ' + child.status + '">' + child.status + '</span></div>' +
      '<div class="body">' + escapeHtml(child.task) + '</div>' +
      (child.output ? '<div class="body">' + escapeHtml(child.output.substring(0, 200)) + (child.output.length > 200 ? '...' : '') + '</div>' : '') +
      '<div class="meta">' + new Date(child.created_at).toLocaleString() + '</div>' +
      promoteBtn;
    list.appendChild(div);
  });
  // Wire the promote buttons. Delegate from the list (single listener)
  // so re-renders don't pile up handlers.
  const buttons = list.querySelectorAll('.child-promote-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => promoteDelegation(btn.dataset.delegationId));
  });
  updateChildrenBadge();
}

async function promoteDelegation(delegationId) {
  if (!delegationId) return;
  try {
    const result = await api('/delegations/' + encodeURIComponent(delegationId) + '/promote', { method: 'POST' });
    if (result && result.status) {
      // Mirror the new status on the local child so the UI updates
      // without waiting for the next WS event.
      const child = state.children.find(c => c.delegation_id === delegationId);
      if (child) {
        child.status = result.status;
        renderChildren();
      }
    }
  } catch (err) {
    showError('Promote failed: ' + (err && err.message ? err.message : err));
  }
}

function updateChildrenBadge() {
  const badge = $('children-badge');
  const running = state.children.filter(c => c.status === 'running').length;
  if (running > 0) {
    badge.textContent = running;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

async function loadAgents() {
  try {
    const data = await api('/agents');
    const grid = $('agents-list');
    grid.innerHTML = '';
    const all = [
      ...(data.builtin || []).map(a => ({...a, scope: 'builtin'})),
      ...(data.global || []).map(a => ({...a, scope: 'global'})),
    ];
    if (all.length === 0) {
      grid.innerHTML = '<div class="empty">No agents configured.</div>';
      return;
    }
    all.forEach(agent => {
      const div = document.createElement('div');
      div.className = 'agent-card ' + agent.scope;
      div.innerHTML =
        '<div class="name">' + escapeHtml(agent.name) + '</div>' +
        '<span class="scope">' + agent.scope + '</span>' +
        '<div class="meta">' +
          '<div class="meta-row"><span>Role</span><span class="v">' + escapeHtml(agent.role || agent.name) + '</span></div>' +
          '<div class="meta-row"><span>Model</span><span class="v">' + escapeHtml(agent.model || 'default') + '</span></div>' +
          '<div class="meta-row"><span>Harness</span><span class="v">' + escapeHtml(agent.harness || 'opencode') + '</span></div>' +
        '</div>';
      grid.appendChild(div);
    });
  } catch (e) { console.error('Load agents:', e); }
}

async function loadMemoryBanks() {
  try {
    const data = await api('/memory/banks');
    state.memoryBanks = data.banks || [];
    const select = $('memory-bank');
    select.innerHTML = '';
    state.memoryBanks.forEach(bank => {
      const opt = document.createElement('option');
      opt.value = bank.id;
      opt.textContent = bank.name + ' (' + bank.scope + ')';
      select.appendChild(opt);
    });
    if (state.memoryBanks.length > 0 && !state.currentMemoryBank) {
      state.currentMemoryBank = state.memoryBanks[0].id;
    }
    if (state.currentMemoryBank) {
      select.value = state.currentMemoryBank;
    }
  } catch (e) { console.error('Load memory banks:', e); }
}

function loadSettings() {
  // Load models
  api('/models').then(data => {
    state.models = data.roles || {};
    const container = $('model-roles');
    container.innerHTML = '';
    Object.entries(state.models).forEach(([role, config]) => {
      const all = Object.values(state.models).flatMap(r => [r.default, ...(r.aliases || [])]);
      const unique = [...new Set(all)];
      const row = document.createElement('div');
      row.className = 'model-role-row';
      row.innerHTML = '<span class="name">' + escapeHtml(role) + '</span>' +
        '<span class="role-desc">' + escapeHtml(getRoleDesc(role)) + '</span>' +
        '<select class="select" onchange="window.sweaveUpdateModel(\'' + role + '\', this.value)">' +
        unique.map(m => '<option value="' + escapeHtml(m) + '"' + (m === config.default ? ' selected' : '') + '>' + escapeHtml(m) + '</option>').join('') +
        '</select>';
      container.appendChild(row);
    });
  }).catch(e => console.error('Load models:', e));

  // Load routing
  api('/rules').then(data => {
    state.rules = data;
    const container = $('routing-rules');
    container.innerHTML = '';
    if (!data.routes || data.routes.length === 0) {
      container.innerHTML = '<p style="color:var(--muted);padding:12px">No rules.</p>';
      return;
    }
    data.routes.forEach((rule, idx) => {
      const row = document.createElement('div');
      row.className = 'rule-row';
      row.innerHTML = '<input class="input pattern" value="' + escapeHtml(rule.pattern) + '" disabled>' +
        '<input class="input" value="' + escapeHtml(rule.agent) + '" disabled>' +
        '<input class="input" value="' + escapeHtml(rule.model || '') + '" placeholder="Model" disabled>';
      container.appendChild(row);
    });
  }).catch(e => console.error('Load rules:', e));

  // Load memory settings
  const memContainer = $('memory-settings-form');
  const memConfig = state.config.memory?.hindsight;
  if (memConfig) {
    memContainer.innerHTML =
      '<div class="model-role-row"><span class="name">Mode</span><span class="role-desc">embedded_slim, docker_full, docker_slim, cloud</span><input class="input" value="' + escapeHtml(memConfig.mode) + '" disabled></div>' +
      '<div class="model-role-row"><span class="name">Bank</span><span class="role-desc">Memory bank ID</span><input class="input" value="' + escapeHtml(memConfig.bank_id || '') + '" disabled></div>';
  }

  // Load theme
  loadThemeSettings();
}

function getRoleDesc(role) {
  const descs = {
    orchestrator: 'Fast coordinator',
    backend: 'APIs, databases, server',
    frontend: 'UI components, React/Vue',
    reviewer: 'Code review, security, quality',
  };
  return descs[role] || '';
}

// ============================================
// Chat
// ============================================
async function sendChat() {
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text || !state.session) return;
  input.value = '';
  $('send-btn').disabled = true;

  // Add user message
  const container = $('chat-messages');
  const empty = container.querySelector('.empty');
  if (empty) empty.remove();
  container.appendChild(createMessage('user', text));
  container.scrollTop = container.scrollHeight;

  // Working indicator
  const working = createMessage('assistant', 'Working...');
  container.appendChild(working);

  try {
    await api('/sessions/' + state.session.id + '/messages', {
      method: 'POST', body: { role: 'user', content: text }
    });
    const result = await api('/tasks', { method: 'POST', body: { task: text } });
    working.remove();
    await api('/sessions/' + state.session.id + '/messages', {
      method: 'POST',
      body: { role: 'assistant', content: result.output || ('Error: ' + (result.error || 'no output')), agent: result.agent }
    });
    const session = await api('/sessions/' + state.session.id);
    state.session = session;
    state.children = session.children || [];
    renderSessionMessages(session);
    renderChildren();
    toast(result.success ? 'Task done' : 'Task failed', result.success ? 'success' : 'error');
  } catch (e) {
    working.remove();
    toast('Failed: ' + e.message, 'error');
  } finally {
    $('send-btn').disabled = false;
  }
}

function createMessage(role, content) {
  const div = document.createElement('div');
  div.className = 'message ' + role;
  const avatar = role === 'user' ? 'U' : 'A';
  div.innerHTML = '<div class="avatar">' + avatar + '</div>' +
    '<div class="content"><div class="role">' + role + '</div><div class="text">' + escapeHtml(content) + '</div></div>';
  return div;
}

// ============================================
// Memory
// ============================================
async function doRecall() {
  const q = $('recall-q').value.trim();
  if (!q || !state.currentMemoryBank) return;
  const container = $('memory-results');
  container.innerHTML = '<div class="empty">Searching...</div>';
  try {
    const res = await api('/memory/recall', { method: 'POST', body: { query: q, bank_id: state.currentMemoryBank } });
    const mems = res.memories || res.results || [];
    container.innerHTML = '';
    if (mems.length === 0) {
      container.innerHTML = '<div class="empty">No memories found.</div>';
      return;
    }
    mems.forEach(m => {
      const div = document.createElement('div');
      div.className = 'memory-result';
      div.textContent = typeof m === 'string' ? m : (m.content || JSON.stringify(m));
      container.appendChild(div);
    });
  } catch (e) {
    container.innerHTML = '<div class="empty">Error: ' + e.message + '</div>';
  }
}

async function doReflect() {
  const q = $('reflect-q').value.trim();
  if (!q || !state.currentMemoryBank) return;
  const container = $('memory-results');
  container.innerHTML = '<div class="empty">Reflecting...</div>';
  try {
    const res = await api('/memory/reflect', { method: 'POST', body: { query: q, bank_id: state.currentMemoryBank } });
    container.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'memory-result';
    div.textContent = res.reflection || res.text || 'No reflection';
    container.appendChild(div);
  } catch (e) {
    container.innerHTML = '<div class="empty">Error: ' + e.message + '</div>';
  }
}

async function doRetain() {
  const c = $('retain-c').value.trim();
  if (!c || !state.currentMemoryBank) return;
  const t = $('retain-t').value;
  const tags = t.split(',').map(s => s.trim()).filter(Boolean);
  try {
    await api('/memory/retain', { method: 'POST', body: { content: c, tags, bank_id: state.currentMemoryBank } });
    toast('Memory retained', 'success');
    $('retain-c').value = '';
    $('retain-t').value = '';
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ============================================
// File Browser
// ============================================
async function openProjectModal() {
  $('proj-name').value = '';
  $('proj-desc').value = '';
  $('proj-new-folder').checked = false;
  state.fbSelected = null;
  $('project-modal').classList.remove('hidden');
  try {
    const res = await api('/fs/drives');
    const drives = res.drives || [];
    const container = $('fb-drives');
    container.innerHTML = '';
    drives.forEach(drive => {
      const btn = document.createElement('button');
      btn.className = 'fb-drive';
      btn.textContent = drive.name;
      btn.onclick = () => loadFBPath(drive.path);
      container.appendChild(btn);
    });
    if (drives.length > 0) {
      loadFBPath(drives[0].path);
    }
  } catch (e) {
    toast('Failed to load drives: ' + e.message, 'error');
  }
}

async function loadFBPath(path) {
  if (!path) return;
  try {
    const res = await api('/fs/list?path=' + encodeURIComponent(path));
    state.fbPath = res.path;
    $('fb-path').value = res.path;
    const list = $('fb-list');
    list.innerHTML = '';
    const entries = res.entries || [];
    if (entries.length === 0) {
      list.innerHTML = '<div class="fb-empty">No folders</div>';
      return;
    }
    entries.forEach(entry => {
      const div = document.createElement('div');
      div.className = 'fb-entry' + (entry.name === '..' ? ' parent' : '') + (entry.path === state.fbSelected ? ' selected' : '');
      div.innerHTML = '<span class="name">' + escapeHtml(entry.name) + '</span>';
      div.onclick = () => {
        if (entry.name === '..') {
          loadFBPath(entry.path);
        } else {
          state.fbSelected = entry.path;
          $('proj-name').value = entry.name;
          renderFBList();
        }
      };
      div.ondblclick = () => {
        if (entry.name !== '..') loadFBPath(entry.path);
      };
      list.appendChild(div);
    });
  } catch (e) {
    toast('Cannot access: ' + e.message, 'error');
  }
}

function renderFBList() {
  loadFBPath(state.fbPath);
}

async function doOpenProject() {
  const name = $('proj-name').value.trim();
  const desc = $('proj-desc').value.trim();
  const createNew = $('proj-new-folder').checked;
  if (!name) { toast('Name required', 'error'); return; }

  let projectPath = null;
  if (createNew) {
    try {
      const res = await api('/fs/create', { method: 'POST', body: { path: state.fbPath, name } });
      projectPath = res.path;
    } catch (e) {
      toast('Failed to create folder: ' + e.message, 'error');
      return;
    }
  } else {
    projectPath = state.fbSelected || state.fbPath;
    if (!projectPath) { toast('Select a folder or enable "Create new folder"', 'error'); return; }
  }

  try {
    await api('/projects', { method: 'POST', body: { name, path: projectPath, description: desc } });
    $('project-modal').classList.add('hidden');
    await openProject(name);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ============================================
// Theme
// ============================================
const THEMES = [
  { id: 'dark', name: 'Dark', bg: '#1a1b1e', fg: '#d4d4d4', panel: '#212226', border: '#3a3d42', red: '#e06c75' },
  { id: 'light', name: 'Light', bg: '#ffffff', fg: '#1e1e1e', panel: '#f5f5f5', border: '#d0d0d0', red: '#c0392b' },
  { id: 'dracula', name: 'Dracula', bg: '#282a36', fg: '#f8f8f2', panel: '#21222c', border: '#44475a', red: '#ff5555' },
  { id: 'nord', name: 'Nord', bg: '#2e3440', fg: '#d8dee9', panel: '#3b4252', border: '#4c566a', red: '#bf616a' },
  { id: 'catppuccin', name: 'Catppuccin', bg: '#1e1e2e', fg: '#cdd6f4', panel: '#181825', border: '#313244', red: '#f38ba8' },
];

function applyTheme(themeId) {
  const t = THEMES.find(x => x.id === themeId);
  if (!t) return;
  document.documentElement.setAttribute('data-theme', themeId);
  const root = document.documentElement.style;
  root.setProperty('--bg', t.bg);
  root.setProperty('--fg', t.fg);
  root.setProperty('--panel', t.panel);
  root.setProperty('--border', t.border);
  root.setProperty('--red', t.red);
  localStorage.setItem('sweave-theme', themeId);
  renderThemeGrid();
}

function loadThemeSettings() {
  renderThemeGrid();
}

function renderThemeGrid() {
  const grid = $('theme-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const current = localStorage.getItem('sweave-theme') || 'dark';
  THEMES.forEach(t => {
    const card = document.createElement('div');
    card.className = 'theme-card' + (t.id === current ? ' active' : '');
    card.innerHTML = '<div class="theme-preview" style="background:' + t.bg + ';color:' + t.red + '">Aa</div><div class="theme-name">' + t.name + '</div>';
    card.onclick = () => applyTheme(t.id);
    grid.appendChild(card);
  });
}

// ============================================
// Models
// ============================================
async function updateModel(role, model) {
  try {
    await api('/models', { method: 'POST', body: { role, model } });
    toast(role + ' → ' + model, 'success');
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}
window.sweaveUpdateModel = updateModel;

// ============================================
// WebSocket
// ============================================
function connectWebSocket() {
  try {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    state.ws = new WebSocket(protocol + '//' + window.location.host + '/ws');
    state.ws.onopen = () => {
      $('status-dot').className = 'status-dot connected';
      $('conn-text').textContent = 'Connected';
    };
    state.ws.onclose = () => {
      $('status-dot').className = 'status-dot';
      $('conn-text').textContent = 'Disconnected';
      setTimeout(connectWebSocket, 3000);
    };
    state.ws.onerror = () => {
      $('status-dot').className = 'status-dot';
      $('conn-text').textContent = 'Error';
    };
    state.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'child_session_update' && state.session) {
          api('/sessions/' + state.session.id).then(s => {
            if (s) {
              state.session = s;
              state.children = s.children || [];
              renderSessionMessages(s);
              renderChildren();
            }
          });
        }
      } catch (err) {}
    };
  } catch (e) {
    console.error('WebSocket error:', e);
  }
}

// ============================================
// EVENT LISTENERS - ALL HERE
// ============================================
function setupEventListeners() {
  console.log('[sweave] Setting up event listeners');

  // Sidebar navigation - THE CRITICAL PART
  $$('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const tab = item.dataset.tab;
      console.log('[sweave] Nav click:', tab);
      switchTab(tab);
    });
  });

  // Sidebar toggle
  $('sidebar-toggle').addEventListener('click', () => {
    $('app').classList.toggle('collapsed');
  });

  // Topbar
  $('project-link').addEventListener('click', openProjectModal);
  $('session-link').addEventListener('click', () => {
    if (!state.project) { toast('Open a project first', 'warning'); return; }
    openSessionModal();
  });
  $('new-session-btn').addEventListener('click', createNewSession);

  // Welcome actions
  $('btn-open-folder').addEventListener('click', openProjectModal);
  $('btn-create-project').addEventListener('click', openProjectModal);
  $('btn-recent').addEventListener('click', loadRecentProjects);

  // Settings tabs
  $$('.settings-tab').forEach(t => {
    t.addEventListener('click', () => switchSettingsTab(t.dataset.stab));
  });

  // Memory tabs
  $('m-tab-recall').addEventListener('click', () => switchMemoryTab('recall'));
  $('m-tab-reflect').addEventListener('click', () => switchMemoryTab('reflect'));
  $('m-tab-retain').addEventListener('click', () => switchMemoryTab('retain'));

  // Chat
  $('chat-input').addEventListener('input', () => {
    $('send-btn').disabled = !$('chat-input').value.trim();
  });
  $('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $('send-btn').addEventListener('click', sendChat);

  // Memory buttons
  $('recall-btn').addEventListener('click', doRecall);
  $('reflect-btn').addEventListener('click', doReflect);
  $('retain-btn').addEventListener('click', doRetain);
  $('memory-bank').addEventListener('change', (e) => {
    state.currentMemoryBank = e.target.value;
  });

  // Project modal
  $('open-project-btn').addEventListener('click', doOpenProject);
  $('fb-up').addEventListener('click', () => {
    if (state.fbPath) {
      const sep = state.fbPath.includes('\\') ? '\\' : '/';
      const parts = state.fbPath.split(sep);
      if (parts.length > 1) parts.pop();
      loadFBPath(parts.join(sep) || sep);
    }
  });
  $('fb-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadFBPath(e.target.value.trim());
  });
  $('proj-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doOpenProject();
  });

  // Session modal
  $('new-session-modal-btn').addEventListener('click', createNewSession);

  // Modal close handlers
  document.querySelectorAll('[data-close]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const modal = e.target.closest('.modal');
      if (modal) modal.classList.add('hidden');
    });
  });
  document.querySelectorAll('.modal-bg').forEach(bg => {
    bg.addEventListener('click', () => {
      const modal = bg.closest('.modal');
      if (modal) modal.classList.add('hidden');
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal:not(.hidden)').forEach(m => m.classList.add('hidden'));
    }
  });

  console.log('[sweave] Event listeners ready');
}

function openSessionModal() {
  $('session-modal').classList.remove('hidden');
  loadSessions();
}

async function loadSessions() {
  if (!state.project) return;
  try {
    const res = await api('/sessions?project_name=' + encodeURIComponent(state.project.name));
    const sessions = res.sessions || [];
    const container = $('session-list');
    container.innerHTML = '';
    if (sessions.length === 0) {
      container.innerHTML = '<p style="color:var(--muted);padding:12px">No sessions.</p>';
      return;
    }
    sessions.forEach(s => {
      const btn = document.createElement('button');
      btn.className = 'recent-item';
      btn.style.width = '100%';
      btn.innerHTML = '<span>' + escapeHtml(s.name) + '</span><span class="path">' + new Date(s.updated_at).toLocaleString() + '</span>';
      btn.onclick = async () => {
        await switchSession(s.id);
        $('session-modal').classList.add('hidden');
      };
      container.appendChild(btn);
    });
  } catch (e) { console.error('Load sessions:', e); }
}

// ============================================
// START - Wait for DOM to be ready
// ============================================
function start() {
  console.log('[sweave] Starting...');
  
  // Load saved theme
  const savedTheme = localStorage.getItem('sweave-theme') || 'dark';
  applyTheme(savedTheme);
  
  // Setup ALL event listeners
  setupEventListeners();
  
  // Connect WebSocket
  connectWebSocket();
  
  // Load context
  loadContext();
  
  // Hide loader AND show app
  setTimeout(() => {
    const loader = $('loader');
    if (loader) {
      loader.style.opacity = '0';
      setTimeout(() => loader.remove(), 300);
    }
    // CRITICAL: Remove hidden class from app so it's visible
    const app = $('app');
    if (app) app.classList.remove('hidden');
  }, 500);
  
  console.log('[sweave] Started');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}

})();
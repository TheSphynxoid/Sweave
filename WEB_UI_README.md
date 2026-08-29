# Sweave Web UI

A modern, developer-focused web interface for Sweave, inspired by [Odysseus](https://github.com/odysseus).

## Features

### Core
- **Single-page application** with 6 tabs: Chat, Agents, Tasks, Worktrees, Memory, Settings
- **WebSocket** real-time updates from the backend
- **Dynamic theming** with 8 preset themes + custom color picker
- **Persistent state** via localStorage
- **Responsive design** for desktop and mobile
- **Keyboard shortcuts** (Enter to send, Shift+Enter for new line, Escape to close modals)

### Tabs

#### Chat
- Direct conversation interface
- Auto-resizing textarea
- Streaming indicator support
- Message history
- Quick-action chips for common tasks

#### Agents
- Grid view of built-in and custom agents
- Create new agents with custom prompts
- Edit/delete dynamic agents
- View agent details (system prompt, tools, model)
- Real-time updates via WebSocket

#### Tasks
- Table with search/filter
- Status indicators (success/error/running)
- Quick view of task details
- Click to see full output

#### Worktrees
- Active worktree list
- Create PR from worktree
- Remove worktrees
- Auto-refresh

#### Memory
- Recall (search memories)
- Reflect (synthesize insights)
- Retain (store new memories)
- Bank selector
- Tag-based filtering

#### Settings
- **Models**: Per-role model configuration with hot-reload
- **Routing**: Edit rules, add new patterns, configure fallback
- **Harness**: Default harness + OpenCode command config
- **Memory**: Hindsight mode (embedded/docker/cloud), embeddings/reranker providers
- **Git**: Provider, worktree base path, auto-PR
- **Server**: Host/port
- **Theme**: Preset themes, custom colors, font, density, background patterns

### Theming
- **8 preset themes**: Dark, Light, Dracula, Nord, Solarized, GitHub, Monokai, Tokyo Night, Catppuccin
- **Custom color picker** for all core colors (bg, fg, panel, border, red)
- **Advanced color overrides** for fine-grained control
- **Color harmony generator** (complementary, analogous, triadic, monochromatic)
- **Font selection**: Monospace (Fira Code), Sans-serif, Serif
- **Density modes**: Compact, Comfortable, Spacious
- **Background patterns**: Solid, Dots, Synapse grid (canvas patterns: Constellations, Perlin Flow, Petals, Sparkles, Rain, Embers)

### Architecture
- **Modular ES6** JavaScript
- **CSS variables** for dynamic theming (no rebuild needed)
- **FastAPI backend** serves static files + REST API + WebSocket
- **No build step** required (vanilla JS, no bundler)
- **Self-hosted fonts** (fallback to system fonts if not available)

## File Structure
```
sweave/web/static/
├── index.html              # Single-page app (all HTML)
├── style.css               # Complete theme system + components
└── js/
    ├── app.js              # Main application logic
    ├── api.js              # API client
    ├── theme.js            # Theme manager (8 presets, custom colors, harmony)
    ├── websocket.js        # WebSocket manager
    ├── modal.js            # Modal manager
    └── notification.js     # Notification system
```

## Usage

```bash
# Start the web server
sweave web --port 8080

# Or with custom host
sweave web --host 0.0.0.0 --port 8080
```

Then open `http://127.0.0.1:8080` in your browser.

## API Endpoints Used

- `GET /api/agents` - List all agents
- `POST /api/agents` - Create agent
- `PUT /api/agents/{name}` - Update agent
- `DELETE /api/agents/{name}` - Delete agent
- `POST /api/tasks` - Execute task
- `POST /api/route` - Preview routing
- `GET /api/worktrees` - List worktrees
- `POST /api/worktrees/pr` - Create PR
- `DELETE /api/worktrees/{task_id}/{agent}` - Remove worktree
- `POST /api/memory/recall` - Search memories
- `POST /api/memory/retain` - Store memory
- `POST /api/memory/reflect` - Synthesize memories
- `GET /api/models` - Get model config
- `POST /api/models` - Set model
- `GET /api/rules` - Get routing rules
- `POST /api/rules` - Add routing rule
- `GET /api/config` - Get full config
- `WS /ws` - WebSocket for real-time updates

## File Access (Agent Tools)

The web agents access files through the **backend** (FastAPI), not the browser:

```
Web UI (browser) → REST/WebSocket → FastAPI Backend → OpenCode Harness → Agent Tools (read/write/bash/glob/grep)
```

**Available tools** (via OpenCode harness):
- `bash` - Execute shell commands
- `read` - Read files
- `write` - Write/create files
- `edit` - Edit files
- `glob` - Find files by pattern
- `grep` - Search file contents
- `webfetch` - Fetch web content
- `websearch` - Web search
- `lsp` - Language server protocol (code intelligence)
- `skill` - Load custom skills

**Security**: All agent file operations happen on your machine through the backend, with full audit trail via the task history.

## Testing

```bash
# Run the test suite
python test_server.py

# Verify HTML structure
python verify_html.py
```

Both scripts will:
1. Start the server
2. Run all tests
3. Stop the server cleanly

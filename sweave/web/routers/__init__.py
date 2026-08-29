"""HTTP route modules for the Sweave web server.

Each submodule owns the routes for one domain (projects/sessions, agents,
tasks, worktrees, memory, config, fs, delegations). Routers expose an
``APIRouter`` and a ``register(app, state)`` helper that mounts the router
on *app* and stores any handles it needs onto *app.state*.

The split landed in M1.prep step 2; before then all routes lived in
``sweave.web.server``.
"""

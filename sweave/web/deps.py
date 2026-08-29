"""FastAPI dependencies for the Sweave web server.

Routers call :func:`get_state` (or the specialised helpers below) to obtain a
handle on the long-lived :class:`sweave.web.state.AppState`. This keeps the
service objects out of module globals.
"""

from __future__ import annotations

from fastapi import Request

from sweave.web.state import AppState


def get_state(request: Request) -> AppState:
    """Return the :class:`AppState` attached to the current app.

    FastAPI resolves ``request.app.state.app_state`` set in ``lifespan``.
    """
    state: AppState | None = getattr(request.app.state, "app_state", None)
    if state is None:  # pragma: no cover - only hit if lifespan didn't run
        raise RuntimeError("AppState not initialised; lifespan did not run")
    return state

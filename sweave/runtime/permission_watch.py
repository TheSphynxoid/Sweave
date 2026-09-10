"""Permission watcher (M1.12 step 2): the `/event`-bus seam for
pending opencode permission checks.

1.18.29 wire (pinned live 2026-09-10; step-0 probe
``scripts/m1_12_permission_wire_probe.py``):

* **Listing surface = the `/event` bus only.** ``permission.asked``
  fires the moment a tool check resolves to ``ask``; the shape is
  ``{id, sessionID, permission, patterns, metadata, always, ...}``.
  There is NO pending-list GET (every candidate route returns the
  SPA HTML catch-all), no replay — a subscriber only sees asks that
  fire after it connected. The watcher therefore runs continuously
  per opencode serve for the serve's whole lifetime.
* **Reply**: ``POST /session/{sid}/permissions/{rid}`` body
  ``{"response": "once" | "always" | "reject"}`` -> 200 ``true``;
  confirmed on the bus by ``permission.replied``. Any other key is
  a 400 ``Missing key at ["response"]``.
* **Resume-after-once**: the tool executes; the turn's terminal
  content lands as a NEW assistant message (the pre-pause assistant
  message completes empty). Post-reply completion is signalled
  observer-side by the bus ``session.idle`` event; the final text
  is fetched via ``GET /session/{sid}/message`` (message list with
  parts; roles + ``info.time.completed``).

This module owns the standing SSE subscription (liveness/permission
signals from the wire only — ruling 2026-09-10: no sqlite scraping
of opencode's DB in production).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Per-serve SSE reconnect backoff when the stream drops mid-flight
# (serve restart or process kill). Bounded retries so a dead serve
# can't spin forever.
SSE_RETRY_ATTEMPTS = 5
SSE_RETRY_BACKOFF_SECONDS = 2.0

# The reply endpoint + accepted ``response`` values (pinned shapes).
DEFAULT_SSE_RETRY_LIMIT = SSE_RETRY_ATTEMPTS
PERMISSION_REPLY_STATUSES = ("once", "always", "reject")

# Bound for the post-reply ``session.idle`` wait (the resumed turn
# completes server-side; the original message stream does not
# re-deliver the terminal frame). Generous: free models are slow.
IDLE_WAIT_SECONDS = 180.0

# Poll interval for the post-reply wait.
IDLE_POLL_SECONDS = 0.5


def parse_permission_event(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Pull a pending-ask record off a ``permission.asked`` event.

    Defensive shape matching (the probe's matcher, tightened):
    properties must carry an ``id``; ``status`` (when present) must
    be a pending marker. Returns the normalized record or None.
    """
    props: Any = obj.get("properties") or obj.get("data") or {}
    if isinstance(props, dict) and isinstance(props.get("permission"), dict):
        # Defensive alternate nesting (never seen live; the pinned
        # shape keeps the record at properties' top level).
        props = props["permission"]
    if not isinstance(props, dict):
        return None
    rid = props.get("id")
    if not rid:
        return None
    status = str(props.get("status", "pending"))
    if status not in ("pending", "asked", ""):
        return None
    return {
        "id": str(rid),
        "sessionID": str(props.get("sessionID", "")),
        "permission": str(props.get("permission", "")),
        "patterns": list(props.get("patterns") or []),
        "metadata": props.get("metadata")
        if isinstance(props.get("metadata"), dict)
        else {},
        "always": list(props.get("always") or []),
    }


class PermissionWatcher:
    """Live-fire ``/event`` bus subscriber for one opencode serve.

    Kept continuously running for the serve's lifetime (started
    lazily on first use; registry :func:`get_permission_watcher`
    hosts per-base_url instances). Buffers:

    * ``pending``: ask records seen but not yet replied.
    * ``idle_counts``: bump per ``session.idle`` per session, so
      callers can await a completion boundary.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.pending: dict[str, dict[str, Any]] = {}
        self.idle_counts: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -----------------------------------------------------

    def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        attempts = 0
        while attempts < SSE_RETRY_ATTEMPTS:
            connected = False
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET", f"{self.base_url}/event"
                    ) as resp:
                        if resp.status_code >= 400:
                            raise RuntimeError(
                                f"/event {resp.status_code} for {self.base_url}"
                            )
                        connected = True
                        attempts = 0  # a clean connect resets the cap
                        buf = ""
                        async for chunk in resp.aiter_text():
                            if not chunk:
                                continue
                            buf += chunk
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                await self._handle_line(line)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PermissionWatcher(%s): stream error: %s",
                    self.base_url, exc,
                )
            if not connected:
                attempts += 1
            else:
                attempts = 1  # was connected: the stream ended (serve_DOWN?)
                logger.warning(
                    "PermissionWatcher(%s): /event stream ended",
                    self.base_url,
                )
            if attempts >= SSE_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(SSE_RETRY_BACKOFF_SECONDS)

    async def _handle_line(self, line: str) -> None:
        line = line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(obj, dict):
            return
        etype = str(obj.get("type", ""))
        async with self._lock:
            if etype == "permission.asked":
                rec = parse_permission_event(obj)
                if rec is not None:
                    self.pending[rec["id"]] = rec
            elif etype == "permission.replied":
                props = obj.get("properties") or {}
                if isinstance(props, dict) and props.get("requestID"):
                    self.pending.pop(str(props["requestID"]), None)
            elif etype == "session.idle":
                props = obj.get("properties") or {}
                sid = str(props.get("sessionID", "")) if isinstance(props, dict) else ""
                if sid:
                    self.idle_counts[sid] = self.idle_counts.get(sid, 0) + 1

    # ---- queries -------------------------------------------------------

    def pending_for(self, session_id: str) -> list[dict[str, Any]]:
        """Pending asks for one engine session (snapshot)."""
        return [
            rec for rec in self.pending.values()
            if rec["sessionID"] == session_id
        ]

    async def wait_idle(
        self,
        session_id: str,
        baseline: int,
        timeout: float = IDLE_WAIT_SECONDS,
    ) -> bool:
        """True when a NEW ``session.idle`` fired past ``baseline``."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.idle_counts.get(session_id, 0) > baseline:
                return True
            await asyncio.sleep(IDLE_POLL_SECONDS)
        return False

    def idle_snapshot(self, session_id: str) -> int:
        return self.idle_counts.get(session_id, 0)


# ---- module-level registry (keyed by serve base_url) -------------------

_WATCHERS: dict[str, PermissionWatcher] = {}


def get_permission_watcher(base_url: str) -> PermissionWatcher:
    """Watcher per serve, created-on-first-use + started.

    If the previous one crashed out (retry cap), a fresh instance is
    created so a re-spawned serve gets a live subscriber.
    """
    key = base_url.rstrip("/")
    existing = _WATCHERS.get(key)
    if existing is None or not existing.is_running():
        existing = PermissionWatcher(key)
        existing.ensure_started()
        _WATCHERS[key] = existing
    else:
        existing.ensure_started()
    return existing


async def reply_permission_request(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    request_id: str,
    response_value: str,
) -> int:
    """POST the pinned reply shape. Returns the HTTP status.

    Pinned 1.18.29: ``POST /session/{sid}/permissions/{rid}``
    body ``{"response": "once" | "always" | "reject"}`` -> 200 ``true``.
    """
    r = await client.post(
        f"{base_url.rstrip('/')}/session/{session_id}/permissions/{request_id}",
        json={"response": response_value},
        timeout=10.0,
    )
    return r.status_code


async def fetch_messages(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """GET the session's message list (``GET /session/{sid}/message``).

    Returns the raw info dicts (``{id, role, time{completed}, parts}``).
    """
    r = await client.get(
        f"{base_url.rstrip('/')}/session/{session_id}/message",
        timeout=10.0,
    )
    if r.status_code != 200 or r.text.lstrip().startswith("<"):
        return []
    try:
        data = r.json()
    except json.JSONDecodeError:
        return []
    msgs = data.get("data", data) if isinstance(data, dict) else data
    return msgs if isinstance(msgs, list) else []


def final_assistant_text(messages: list[Any]) -> str:
    """Concatenate the text parts of COMPLETED assistant messages.

    The permission flow produces possibly two assistant records (an
    empty one completed at the pause, then the resumed one with the
    content). Completed = ``time.completed`` set. Text parts only.
    """
    texts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        info = m.get("info") if isinstance(m.get("info"), dict) else m
        role = str(info.get("role", ""))
        if role != "assistant":
            continue
        completed = (info.get("time") or {}).get("completed")
        if completed is None:
            continue
        for part in info.get("parts", []) or []:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
    return "".join(texts)

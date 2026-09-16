"""Unified WebSocket event bus.

The :class:`WSEventBus` owns the list of connected WebSocket clients and a
single :meth:`publish` entry point. Publishers (routers, the JobRunner, the
specialist runtime when it lands) call ``bus.publish(event, data)``; the
bus fans the message out to every subscriber and drops dead connections
transparently.

Wire format (unchanged from the pre-M1.prep server):

.. code-block:: json

    {"event": "<name>", "data": {...}, "timestamp": "<iso8601>"}

Event vocabulary (locked in M1.prep; M1.x may add names but should not
change the meaning of the ones below):

* ``delegation.status_changed``   -- ``{delegation_id, status, agent, task_id, ts}``
* ``delegation.output_chunk``     -- ``{delegation_id, chunk, ts}`` (M1.8 emits)
* ``chat.delta``                  -- ``{session_id, delegation_id, text}`` (M1.8 emits;
  coalesced text increments for the in-flight assistant bubble)
* ``chat.thinking``               -- ``{session_id, delegation_id, text}`` (thinking
  capture emits; coalesced reasoning increments for the live Thinking block)
* ``chat.tool``                   -- ``{session_id, delegation_id, round, tool}``
  (tool transparency emits; one event per tool transition — the tool row
  is ``{callID, tool, status, summary, title, input, round}``, latest
  status wins per callID)
* ``message.added``               -- ``{session_id, message}`` (persisted message;
  the assistant copy carries ``metadata.delegation_id`` + ``metadata.thinking``)
* ``specialist.delta``            -- ``{delegation_id, text}`` (view step
  4c emits; coalesced child-turn text increments, keyed by the CHILD
  delegation id — no session_id, a child turn belongs to its parent
  chat via the record, not a Session bubble)
* ``specialist.thinking``         -- ``{delegation_id, text}`` (same
  capture, coalesced reasoning increments for the child's live block)
* ``specialist.tool``             -- ``{delegation_id, tool}`` (one event
  per tool transition; tool row like chat.tool's but round 0, child-id
  keyed — the open card/modal tracks the running specialist's tools)
* ``specialist.idle``             -- ``{name, model, ts}`` (M1.3 emits)
* ``specialist.running``          -- ``{name, model, task_id, ts}`` (M1.3 emits)
* ``model.changed``               -- ``{role, model, ts}``

Legacy aliases (preserved on the wire so today's UI keeps working; new
code SHOULD prefer the unified names above):

* ``agent_created`` / ``agent_updated`` / ``agent_deleted``
* ``task_completed``
* ``worktrees_cleaned`` / ``worktree_removed``
* ``rule_added``
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSEventBus:
    """In-process pub/sub for WebSocket subscribers.

    Subscribers are :class:`fastapi.WebSocket` connections. :meth:`publish`
    is safe to call from any coroutine; it acquires the subscribers lock,
    fans the message out, and removes any connection whose send fails
    (closed by peer, network drop, etc.).
    """

    def __init__(self) -> None:
        self._subscribers: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.append(ws)

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(ws)
            except ValueError:
                pass

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        """Fan *event* out to every connected subscriber.

        Returns immediately; slow consumers do not block publishers. Dead
        connections (send raised) are dropped from the subscriber list.
        """
        envelope = {
            "event": event,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            payload = json.dumps(envelope)
        except (TypeError, ValueError) as e:
            logger.warning("WS publish: non-serialisable payload for %s: %s", event, e)
            return

        # Snapshot under the lock so we don't hold it across the network I/O.
        async with self._lock:
            targets = list(self._subscribers)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    try:
                        self._subscribers.remove(ws)
                    except ValueError:
                        pass

    @property
    def subscriber_count(self) -> int:
        """Approximate count for diagnostics (read-only, may be stale)."""
        return len(self._subscribers)

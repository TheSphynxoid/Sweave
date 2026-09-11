"""Coalescer for M1.8 chat.delta events.

Per the plan: "flush at most every ~100ms or ~64 chars". The
coalescer buffers incremental text and emits a single event per
flush window. The goal is to avoid per-token WS spam while still
giving the user the illusion of streaming.

The coalescer is the **runtime's view** -- it knows when to flush,
not the LLM. The LLM is the producer (text parts via the
harness's on_chunk callback). The runtime is the consumer that
turns those parts into coalesced events on the bus.

The coalescer runs in the background as an asyncio task: a single
producer pushes parts (synchronously) and a single consumer task
flushes the buffer on a timer. Stop() is idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class ChatDeltaCoalescer:
    """Buffer incremental text and emit coalesced events.

    Producer side: ``push(text)`` is called from the chat loop's
    on_chunk callback (synchronous, never blocks). The text is
    appended to a buffer.

    Consumer side: a background task wakes every
    ``flush_interval_ms`` and emits a single ``chat.delta`` event
    with the buffered text (if any). The event payload includes
    ``session_id`` and ``delegation_id`` so the UI can scope
    updates to the right bubble.

    The coalescer also emits when the buffer crosses the
    ``char_threshold`` -- a hard cap that prevents a runaway
    stream from waiting the full ``flush_interval_ms`` before the
    user sees anything.

    The lifecycle:
    * ``start()`` launches the background flush task.
    * ``push(text)`` buffers text.
    * ``close_and_flush()`` cancels the task, flushes the final
      buffer, returns. Idempotent: callers may call it more than
      once without error.
    """

    def __init__(
        self,
        *,
        emit: Callable[[str], Awaitable[None] | None],
        flush_interval_ms: int = 100,
        char_threshold: int = 64,
    ) -> None:
        self._emit = emit
        self._flush_interval = flush_interval_ms / 1000.0
        self._char_threshold = char_threshold
        self._buffer: list[str] = []
        self._buffer_chars = 0
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    def start(self) -> None:
        """Launch the background flush task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._flush_loop())

    def push(self, text: str) -> None:
        """Buffer *text*. Synchronous, non-blocking.

        The producer (the chat loop's on_chunk callback) calls
        this with each text part as it leaves the harness. The
        background flush task drains the buffer and emits.

        When the buffer crosses ``char_threshold`` the flush is
        scheduled immediately (a task for :meth:`_flush_once`)
        instead of waiting for the next timer tick -- the "hard
        cap" that keeps a bursty stream visibly incremental.
        """
        if self._stopped or not text:
            return
        self._buffer.append(text)
        self._buffer_chars += len(text)
        if self._buffer_chars >= self._char_threshold:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # no loop (tests / shutdown); the timer flushes
            if not self._stopped:
                loop.create_task(self._flush_once())

    async def _flush_loop(self) -> None:
        """Background consumer: flush the buffer on a timer.

        Wakes every ``flush_interval_ms`` (or sooner if the
        char_threshold is hit -- that's the producer's check,
        called from push()). Emits the buffered text as a single
        chat.delta event.

        The timer is intentionally slightly-fuzzy; the goal is
        "at most every 100ms", not "exactly every 100ms".
        """
        try:
            while not self._stopped:
                await asyncio.sleep(self._flush_interval)
                if self._buffer_chars > 0:
                    await self._flush_once()
        except asyncio.CancelledError:
            # Final flush on cancellation
            if self._buffer_chars > 0:
                try:
                    await self._flush_once()
                except Exception:  # noqa: BLE001
                    pass
            raise

    async def flush(self) -> None:
        """Emit the buffered text now.

        Mid-turn use (multi-message turns): the chat loop flushes
        the round-0 buffer before advancing the round so deltas
        attribute to the round that produced them instead of the
        round that happened to be live at the next timer tick.
        """
        await self._flush_once()

    async def _flush_once(self) -> None:
        async with self._lock:
            if self._buffer_chars == 0:
                return
            text = "".join(self._buffer)
            self._buffer.clear()
            self._buffer_chars = 0
        try:
            result = self._emit(text)
            if hasattr(result, "__await__"):
                await result
        except Exception as e:  # noqa: BLE001
            logger.warning("ChatDeltaCoalescer: emit failed: %s", e)

    async def close_and_flush(self) -> None:
        """Stop the background task and emit the final buffer.

        Idempotent. Safe to call multiple times. Safe to call
        before the background task has been created (no-op).
        """
        if self._stopped:
            return
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.warning("ChatDeltaCoalescer: task exited: %s", e)
            self._task = None
        # Final flush
        if self._buffer_chars > 0:
            try:
                await self._flush_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("ChatDeltaCoalescer: final flush failed: %s", e)

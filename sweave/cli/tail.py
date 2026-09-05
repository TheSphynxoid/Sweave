"""``sweave tail <delegation_id>`` -- follow a running turn.

Reads ``~/.sweave/traces/{delegation_id}.jsonl`` as it grows, yielding
each new line. The CLI wraps this generator in a console printer; the
generator itself is the seam for the test and any future watcher
(e.g. an editor panel that wants the raw JSONL stream).

The file is opened with ``O_RDONLY``; we track the byte offset and
read forward on each ``poll`` (default 100ms). The generator exits
cleanly when the caller closes it (the CLI catches KeyboardInterrupt
and stops the loop).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator


async def follow_trace(
    path: Path,
    *,
    from_start: bool = False,
    poll_interval: float = 0.1,
) -> AsyncIterator[str]:
    """Yield each line of *path* as it's appended.

    * ``from_start=True`` -- read the entire file first, then follow.
    * ``from_start=False`` -- skip existing content; only yield lines
      that arrive after the generator is created.

    The generator polls the file every ``poll_interval`` seconds.
    Cancelling the consumer (via ``aclose()`` or task cancellation)
    stops the loop cleanly.
    """
    if not path.exists():
        return
    offset = 0 if from_start else path.stat().st_size
    try:
        with path.open("r", encoding="utf-8") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if line:
                    yield line.rstrip("\n")
                else:
                    await asyncio.sleep(poll_interval)
                    # Detect file truncation/rotation: if the file is
                    # shorter than our offset, reset.
                    try:
                        cur_size = path.stat().st_size
                    except OSError:
                        return
                    if cur_size < offset:
                        f.seek(0)
                        offset = 0
                    else:
                        offset = cur_size
    except (asyncio.CancelledError, GeneratorExit):
        return
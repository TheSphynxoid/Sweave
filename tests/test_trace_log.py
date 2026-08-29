"""TraceLog tests."""

from __future__ import annotations

import json
from pathlib import Path

from sweave.runtime.trace_log import TraceLog, read_trace


def test_write_and_read_back(tmp_path: Path):
    log = TraceLog("d-1", base_dir=tmp_path)
    log.append("status_changed", {"status": "queued"})
    log.append("prompt_sent", {"prompt": "hello"})
    log.append("status_changed", {"status": "done"})
    log.close()

    events = read_trace("d-1", base_dir=tmp_path)
    assert len(events) == 3
    assert events[0]["event"] == "status_changed"
    assert events[0]["status"] == "queued"
    assert events[2]["status"] == "done"
    # Every event has the delegation id + ts
    for e in events:
        assert e["delegation_id"] == "d-1"
        assert "ts" in e


def test_read_missing_returns_empty(tmp_path: Path):
    assert read_trace("does-not-exist", base_dir=tmp_path) == []


def test_read_skips_malformed_lines(tmp_path: Path):
    log = TraceLog("d-2", base_dir=tmp_path)
    log.append("ok", {"k": 1})
    log.close()
    # Manually append garbage to the file
    path = log.path
    with path.open("a", encoding="utf-8") as f:
        f.write("not json\n")
        f.write("\n")
    log.append("ok2", {"k": 2})
    log.close()
    events = read_trace("d-2", base_dir=tmp_path)
    assert len(events) == 2
    assert [e["event"] for e in events] == ["ok", "ok2"]


def test_context_manager_closes(tmp_path: Path):
    with TraceLog("d-3", base_dir=tmp_path) as log:
        log.append("e", {"k": 1})
    # File should exist and be readable
    events = read_trace("d-3", base_dir=tmp_path)
    assert len(events) == 1


def test_concurrent_writes_are_atomic_per_line(tmp_path: Path):
    """Multiple threads can call append safely (one line at a time)."""
    import threading

    log = TraceLog("d-4", base_dir=tmp_path)
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        barrier.wait()
        for i in range(20):
            log.append("ev", {"thread": n, "i": i})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()
    events = read_trace("d-4", base_dir=tmp_path)
    assert len(events) == 8 * 20, f"expected 160 events, got {len(events)}"
    # Every line must be valid JSON
    for e in events:
        json.dumps(e)  # raises if malformed

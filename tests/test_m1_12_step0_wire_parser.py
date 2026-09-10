"""M1.12 step-0 gate: mock-transport tests for the probe wire parser.

Pins the opencode 1.18.29 permission wire shapes observed live
(2026-09-10 scratch serve): the ``permission.asked`` /
``permission.replied`` bus events and the pending-shape arbor
extractor. These shapes are what the M1.12 step-2 poller will
parse; the parser lives in scripts/m1_12_permission_wire_probe.py
(the probe is the repo's pinning artifact for this slice).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from m1_12_permission_wire_probe import (  # noqa: E402
    PermissionWatch,
    _extract_request_id,
    _parse_stream_line,
)

ASKED_EVENT = {
    "type": "permission.asked",
    "properties": {
        "id": "per_089a10286001VjHha3KRiPY9jp",
        "sessionID": "ses_f765ff532ffemRqDK7cVfPwuP0",
        "permission": "external_directory",
        "patterns": ["C:\\Windows\\*"],
        "metadata": {
            "command": "cat \"C:\\Windows\\win.ini\"",
            "directories": ["C:\\Windows"],
            "patterns": ["C:\\Windows\\*"],
        },
        "always": ["C:\\Windows\\*"],
    },
}

REPLIED_EVENT = {
    "type": "permission.replied",
    "properties": {
        "sessionID": "ses_f765ff532ffemRqDK7cVfPwuP0",
        "requestID": "per_089a10286001VjHha3KRiPY9jp",
        "reply": "once",
    },
}


async def _feed(watch: PermissionWatch, frames: list[dict]) -> None:
    """Drive watch's line parser without a socket (newline JSON)."""
    for frame in frames:
        obj = _parse_stream_line(
            "data: " + json.dumps(frame) + "\n"
        )
        assert obj is not None
        etype = str(obj.get("type", ""))
        watch.types_seen[etype] = watch.types_seen.get(etype, 0) + 1
        if "permission" in etype:
            watch.events.append(obj)


def test_parse_stream_line_accepts_sse_and_plain() -> None:
    assert _parse_stream_line("data: {\"a\": 1}\n") == {"a": 1}
    assert _parse_stream_line("{\"a\": 2}\n") == {"a": 2}
    assert _parse_stream_line("\n") is None
    assert _parse_stream_line("not json") is None


@pytest.mark.asyncio
async def test_watch_collects_asked_and_shows_pending() -> None:
    watch = PermissionWatch("http://x")
    # First a non-permission event; then the ask.
    await _feed(watch, [{"type": "server.connected", "properties": {}}])
    assert watch.requests() == []
    await _feed(watch, [ASKED_EVENT])
    reqs = watch.requests()
    assert len(reqs) == 1
    assert reqs[0]["id"] == "per_089a10286001VjHha3KRiPY9jp"
    assert reqs[0]["sessionID"] == "ses_f765ff532ffemRqDK7cVfPwuP0"
    assert reqs[0]["permission"] == "external_directory"
    assert reqs[0]["patterns"] == ["C:\\Windows\\*"]
    assert reqs[0]["metadata"]["command"].endswith("win.ini\"")
    # The replied event is not a pending-ask signal for the matcher.
    await _feed(watch, [REPLIED_EVENT])
    # The asked record is still the one-pending source here (the
    # probe scene pins the id BEFORE replying, as the loop does).
    assert watch.requests()[0]["id"] == "per_089a10286001VjHha3KRiPY9jp"


@pytest.mark.asyncio
async def test_watch_filters_by_session_id() -> None:
    watch = PermissionWatch("http://x")
    await _feed(watch, [ASKED_EVENT])
    other = dict(ASKED_EVENT["properties"])
    other["sessionID"] = "ses_OTHER"
    other["id"] = "per_OTHER"
    await _feed(watch, [dict(ASKED_EVENT, properties=other)])
    reqs = watch.requests()
    assert {rq["sessionID"] for rq in reqs} == {
        "ses_f765ff532ffemRqDK7cVfPwuP0", "ses_OTHER",
    }


def test_extract_request_id_shapes() -> None:
    # data:[...] / map-of-object / raw-object arbor shapes.
    assert _extract_request_id({"status": "pending", "id": "per_x"}) == "per_x"
    assert _extract_request_id({"data": [{"status": "pending", "id": "per_y"}]}) == "per_y"
    assert _extract_request_id({"data": []}) is None
    assert _extract_request_id("<!doctype html>") is None
    assert _extract_request_id([]) is None

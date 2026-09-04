"""M1.8 step 3: streaming bubble UI tests, run via node.

The IIFE in app.js wires up a WS handler. The streaming logic
is at the top of the IIFE; this test extracts those functions
by source range, evaluates them in a minimal jsdom polyfill,
and dispatches chat.delta + message.added events to assert the
chat-messages container is updated correctly.

The app.js IIFE's runtime behaviour is also verified by the
live scene at scripts/m1_7_live_scene.py. This test is the
unit-level pin: it asserts the JS DOM manipulation logic in
isolation, so a future refactor that breaks the streaming
lifecycle (create-on-first-delta, patch-on-subsequent-deltas,
replace-on-message.added, ignore-wrong-session) is caught
without spinning up a full server.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


JS_SCRIPT = Path(__file__).parent / "test_m1_8_step3_streaming_ui.js"


@pytest.mark.skipif(
    not JS_SCRIPT.exists(),
    reason="test_m1_8_step3_streaming_ui.js not present",
)
def test_streaming_bubble_lifecycle():
    """Run the JS test; assert the streaming lifecycle works."""
    result = subprocess.run(
        ["node", str(JS_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
        timeout=30,
    )
    out = result.stdout
    err = result.stderr
    assert result.returncode == 0, (
        f"node test failed: stdout={out!r} stderr={err!r}"
    )
    # Spot-check that the key passes are in the output
    for expected in [
        "PASS: one bubble after first delta",
        "PASS: bubble text = hello world",
        "PASS: two bubbles after second delegation",
        "PASS: one bubble still streaming (del-2)",
        "PASS: still two bubbles after replacement",
        "PASS: replaced bubble has no streaming class",
        "PASS: replaced bubble is assistant",
        "PASS: message.added for unknown delegation is a no-op",
        "PASS: user message.added is a no-op",
    ]:
        assert expected in out, (
            f"expected '{expected}' in node output, got: {out!r}"
        )
    # Sanity: the test suite is complete (last line is the
    # ALL GREEN footer)
    assert "ALL GREEN" in out, f"missing ALL GREEN footer: {out!r}"

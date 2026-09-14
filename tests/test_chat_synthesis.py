"""Chat synthesis prompt tests (pure builder).

Consolidated from the M1.7-step-3 suite: ``build_synthesis_prompt``
(server-side) composes a per-child structured prompt with status,
task, output, and error fields; overflows truncate oldest-first;
failed children are included with their error; empty input yields
the empty string (the fast path never builds one).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sweave.chat.synthesis import (
    _approx_tokens,
    build_synthesis_prompt,
    truncate_to_tokens,
)
from sweave.runtime.delegation_store import Delegation


def _make_child(
    *,
    agent: str,
    task: str,
    status: str,
    output: str = "",
    error: str | None = None,
    completed_at: datetime | None = None,
) -> Delegation:
    d = Delegation(
        delegation_id=f"d-{agent}",
        task_id=f"t-{agent}",
        agent=agent,
        model="hy3",
        task=task,
        status=status,
        output=output,
        error=error,
    )
    if completed_at is not None:
        d.completed_at = completed_at
    return d


def test_synthesis_prompt_includes_each_child_status_and_output():
    children = [
        _make_child(agent="backend", task="make hello.py", status="done",
                    output="created hello.py"),
        _make_child(agent="frontend", task="make style.css", status="done",
                    output="created style.css"),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="scaffold a tiny site",
    )
    assert "backend" in prompt
    assert "frontend" in prompt
    assert "done" in prompt
    assert "created hello.py" in prompt
    assert "created style.css" in prompt
    assert "scaffold a tiny site" in prompt


def test_synthesis_prompt_includes_failed_children_with_error():
    """A failed child is not silently dropped -- the orchestrator
    needs to know the failure to acknowledge it.
    """
    children = [
        _make_child(agent="backend", task="make hello.py", status="done",
                    output="ok"),
        _make_child(agent="frontend", task="make style.css", status="failed",
                    error="permission denied"),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="scaffold a tiny site",
    )
    assert "frontend" in prompt
    assert "failed" in prompt
    assert "permission denied" in prompt


def test_synthesis_prompt_truncates_when_overflowing_cap():
    """A child whose output is larger than the per-child budget is
    truncated; the prefix is preserved.
    """
    long_output = "word " * 5_000  # ~6667 tokens at the rough heuristic
    children = [
        _make_child(agent="backend", task="big task", status="done",
                    output=long_output),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="big",
        token_cap=1_000,
    )
    assert "truncated" in prompt
    # The original 5_000 words shouldn't be in the prompt verbatim
    # (they would push the prompt well over the cap).
    assert prompt.count("word") < 5_000


def test_synthesis_prompt_empty_children_returns_empty():
    """No children -> empty string. The fast path doesn't build a
    synthesis prompt.
    """
    assert build_synthesis_prompt(
        children=[], original_user_message="hello"
    ) == ""


def test_truncate_to_tokens_preserves_short_text():
    text = "hello world"
    assert truncate_to_tokens(text, token_cap=100) == text


def test_truncate_to_tokens_cuts_long_text():
    text = "word " * 1_000  # ~1333 tokens
    out = truncate_to_tokens(text, token_cap=100)
    assert len(out) < len(text)
    assert "truncated" in out


def test_approx_tokens_zero_for_empty():
    assert _approx_tokens("") == 0

"""Prompt template variables (``{{var}}``) for specialist system prompts.

Pins:

* Rendering: known names substitute, unknown ``{{names}}`` stay
  verbatim (never a hard error), whitespace-tolerant; ``${VAR}``
  (other agent apps' spelling) is NOT expanded.
* Context: every documented key present; git-derived keys degrade
  to "" outside a repo.
* Runtime hook: templated prompts render fresh per delegation
  (system send every turn); static prompts keep the legacy one-off
  send on session create (no wire change).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.prompt_template import (
    KNOWN_VARIABLES,
    build_template_context,
    build_workspace_line,
    has_template_vars,
    render_prompt_template,
    template_var_names,
)


def test_render_substitutes_known_vars():
    out = render_prompt_template(
        "Work in {{worktree_path}} (task {{task_id}}).",
        {"worktree_path": "/w/t1", "task_id": "abc"},
    )
    assert out == "Work in /w/t1 (task abc)."


def test_render_leaves_unknown_vars_verbatim():
    out = render_prompt_template("Hello {{mystery}} and {{task}}.", {"task": "t"})
    assert out == "Hello {{mystery}} and t."


def test_render_tolerates_whitespace_and_empty_values():
    out = render_prompt_template("[{{  today  }}|{{branch}}]", {"today": "2026-09-09", "branch": ""})
    assert out == "[2026-09-09|]"


def test_dollar_vars_are_not_templates():
    assert not has_template_vars("Use ${WORKING_DIRECTORY} here.")
    assert template_var_names("Use ${WORKING_DIRECTORY} here.") == []
    out = render_prompt_template("Use ${WORKING_DIRECTORY} here.", {"WORKING_DIRECTORY": "x"})
    assert out == "Use ${WORKING_DIRECTORY} here."


def test_has_template_vars():
    assert has_template_vars("dir: {{worktree_path}}")
    assert not has_template_vars("plain static prompt")
    assert not has_template_vars("")


def test_context_covers_documented_vocabulary(tmp_path: Path):
    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.specialist_store import Specialist

    spec = Specialist(name="templ", scope="project", is_orchestrator=False,
                      system_prompt="x", harness="opencode")
    spec.session_id = "ses_1"
    d = Delegation(agent="templ", task="do it", project_name="shop")
    ctx = build_template_context(
        specialist=spec, delegation=d, worktree_path=tmp_path, model="opencode/m",
    )
    for key in KNOWN_VARIABLES:
        assert key in ctx, f"context missing {key!r}"
    assert ctx["specialist"] == "templ"
    assert ctx["agent"] == "templ"
    assert ctx["task"] == "do it"
    assert ctx["worktree_path"] == str(tmp_path)
    assert ctx["model"] == "opencode/m"
    assert ctx["session_id"] == "ses_1"
    # tmp_path is not a git repo: advisory keys degrade to "".
    assert ctx["branch"] == ""
    assert ctx["git_status"] == ""
    assert ctx["recent_commits"] == ""


# ---------------------------------------------------------------------------
# Runtime hook (mock opencode, real SpecialistRuntime.run)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def _make_runtime(monkeypatch, tmp_path: Path):
    """Real runtime on a mock serve; task sends faked, system sends spied."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    system_sends: list[str] = []

    async def fake_send_message(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        return "task-output"

    async def spy_send(self, message, on_chunk=None, trace=None, trace_reasoning=False):
        system_sends.append(f"{message.type}:{message.content}")
        # The real harness invokes on_chunk on streamed text parts;
        # the runtime's first-byte bound (2026-09-13) now depends on
        # it. A double that never touches on_chunk trips the bound
        # after PRE_MODEL_TIMEOUT_SECONDS (16-minute suite hangs).
        if on_chunk is not None:
            on_chunk("ok")
        from unittest.mock import MagicMock

        return MagicMock()

    runtime._send_message = fake_send_message  # type: ignore[assignment]
    monkeypatch.setattr(OpenCodeProcess, "send", spy_send)
    return runtime, system_sends


def _spec(name: str, prompt: str):
    from sweave.runtime.specialist_store import Specialist

    return Specialist(name=name, scope="project", is_orchestrator=False,
                      system_prompt=prompt, harness="opencode")


def _delegation(agent: str, task: str):
    from sweave.runtime.delegation_store import Delegation

    return Delegation(agent=agent, task=task, project_name="shop")


@pytest.mark.asyncio
async def test_templated_prompt_sent_every_turn(monkeypatch, tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    runtime, system_sends = _make_runtime(monkeypatch, tmp_path)
    spec = _spec("templ", "Work in {{worktree_path}} ({{delegation_id}}).")
    for i in range(2):
        d = _delegation("templ", f"task {i}")
        trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
        await runtime.run(
            specialist=spec, delegation=d, worktree_path=tmp_path,
            message=d.task, trace=trace,
        )
        trace.close()
    assert len(system_sends) == 2
    for sent in system_sends:
        assert sent.startswith("system:")
        assert "{{" not in sent
        assert str(tmp_path) in sent
    # Each turn rendered its own delegation id.
    assert system_sends[0] != system_sends[1]


@pytest.mark.asyncio
async def test_static_prompt_sent_once(monkeypatch, tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    runtime, system_sends = _make_runtime(monkeypatch, tmp_path)
    spec = _spec("plain", "You are a plain specialist.")
    for i in range(2):
        d = _delegation("plain", f"task {i}")
        trace = TraceLog(d.delegation_id, base_dir=tmp_path / "traces")
        await runtime.run(
            specialist=spec, delegation=d, worktree_path=tmp_path,
            message=d.task, trace=trace,
        )
        trace.close()
    assert len(system_sends) == 1
    assert system_sends[0] == "system:You are a plain specialist."


# ---------------------------------------------------------------------------
# Policy-aware workspace sentence (review-hardening step 1, 2026-09-15)
# ---------------------------------------------------------------------------


def test_workspace_isolated_owned(tmp_path: Path):
    line = build_workspace_line(
        kind="task", policy="isolated", owned=True,
        worktree_path=str(tmp_path), branch="sweave/abc/backend",
    )
    assert str(tmp_path) in line
    assert "sweave/abc/backend" in line
    assert "yours alone" in line


def test_workspace_inherit_shared_names_owner(tmp_path: Path):
    line = build_workspace_line(
        kind="task", policy="inherit", owned=False,
        parent_task_id="parent-1", worktree_path=str(tmp_path),
        branch="sweave/abc/backend",
    )
    assert str(tmp_path) in line
    assert "parent-1" in line
    assert "NOT yours to retire" in line


def test_workspace_inherit_fallback_is_intentional(tmp_path: Path):
    # Treeless parent (e.g. a chat delegation): the orchestrator's view
    # BY DESIGN (user ruling 2026-09-15) — never phrased as an accident.
    line = build_workspace_line(
        kind="task", policy="inherit", owned=False,
        parent_task_id="chat-9", worktree_path=str(tmp_path), branch="master",
    )
    assert str(tmp_path) in line
    assert "by design" in line
    assert "accident" not in line


def test_workspace_none_policy_is_live_root(tmp_path: Path):
    line = build_workspace_line(
        kind="task", policy="none", owned=False,
        worktree_path=str(tmp_path), branch="master",
    )
    assert "policy `none`" in line
    assert "live tree" in line


def test_workspace_chat_kind_takes_no_tree(tmp_path: Path):
    line = build_workspace_line(
        kind="chat", policy="isolated", owned=True,
        worktree_path=str(tmp_path), branch="master",
    )
    assert "never take one" in line


def test_context_carries_policy_and_workspace(tmp_path: Path):
    from sweave.runtime.delegation_store import Delegation
    from sweave.runtime.specialist_store import Specialist

    spec = Specialist(name="rev", scope="project", is_orchestrator=False,
                      system_prompt="x", harness="opencode",
                      worktree_policy="inherit")
    d = Delegation(agent="rev", task="review it", project_name="shop")
    d.worktree_owned = False
    d.parent_task_id = "chat-9"
    ctx = build_template_context(
        specialist=spec, delegation=d, worktree_path=tmp_path, model="",
    )
    assert ctx["worktree_policy"] == "inherit"
    assert "by design" in ctx["workspace"]
    out = render_prompt_template("{{workspace}} @ {{worktree_path}}", ctx)
    assert "{{" not in out
    assert str(tmp_path) in out


def test_seeds_use_workspace_template_not_hardcoded_paths():
    from sweave.agents.loader import AGENTS_DIR

    for role in ("backend", "frontend", "reviewer"):
        text = (AGENTS_DIR / role / "config.yaml").read_text(encoding="utf-8")
        assert ".worktrees/{task_id}" not in text, f"{role} still hardcodes a worktree path"
        assert "{{workspace}}" in text, f"{role} missing {{{{workspace}}}}"
        assert "{{worktree_path}}" in text, f"{role} missing {{{{worktree_path}}}}"

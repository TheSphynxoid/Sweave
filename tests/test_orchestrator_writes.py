"""Orchestrator `.md` + `todo` widening (2026-09-15 ruling).

Pins:
* Seed: the orchestrator seed parses (the 2026-09-15 repair —
  col-0 list items broke the block scalar and the loader silently
  skipped the file, fossilizing live charters) and its charter
  teaches `.md`-only writes + todo doctrine.
* Runtime: `ORCHESTRATOR_TOOLS` (+edit/+write/+todo, still no bash)
  and the last-match-wins `.md` gate map.
* Asymmetry note: the opencode agent profile still allows file
  writes (2026-09-09 ruling) — `.md`-only is charter-only there,
  structural on the engine. Pinned so a profile change forces a
  doc update, not a silent drift.
"""

from __future__ import annotations


def test_orchestrator_seed_loads():
    from sweave.agents.loader import load_seed_agents

    defs = load_seed_agents(refresh=True)
    assert "orchestrator" in defs
    assert defs["orchestrator"].prompt


def test_orchestrator_charter_teaches_md_and_todo():
    from sweave.agents.loader import load_seed_agents

    prompt = load_seed_agents(refresh=True)["orchestrator"].prompt
    # `.md`-only writes (not a blanket grant).
    assert ".md" in prompt
    assert "never code" in prompt
    # No bash, ever.
    assert "no `bash`, ever" in prompt
    # Todo doctrine: plan steps vs work state + settle rule.
    # (Single-line needles: the charter is a literal block and
    # wraps across lines.)
    assert "Todos track YOUR plan steps" in prompt
    assert "while its delegation is unsettled" in prompt
    # The old blanket ban is narrowed, not present.
    assert "those are specialists' tools" not in prompt


def test_orchestrator_tools_tuple():
    from sweave.runtime.specialist_runtime import (
        ORCHESTRATOR_MD_WRITE_MAP,
        ORCHESTRATOR_READONLY_TOOLS,
        ORCHESTRATOR_TOOLS,
    )

    assert tuple(ORCHESTRATOR_TOOLS) == ("read", "grep", "glob", "edit", "write", "todo", "git")
    assert "bash" not in ORCHESTRATOR_TOOLS
    assert "git" in ORCHESTRATOR_TOOLS  # GIT_READ_TOOL: orchestrator-only
    # The read trio still rides every widening.
    for tool in ORCHESTRATOR_READONLY_TOOLS:
        assert tool in ORCHESTRATOR_TOOLS
    # Last-match-wins: blanket deny, then `*.md` allow.
    assert ORCHESTRATOR_MD_WRITE_MAP == {"*": "deny", "*.md": "allow"}
    assert list(ORCHESTRATOR_MD_WRITE_MAP) == ["*", "*.md"]


def test_opencode_orchestrator_profile_still_allows_files():
    """Documents the charter-only asymmetry (see module docstring)."""
    from sweave.runtime.agent_permission import render_agent_permission_profile

    profile = render_agent_permission_profile(is_orchestrator=True)
    assert "edit" not in profile and "write" not in profile
    assert profile["bash"]  # git-mutation denies intact

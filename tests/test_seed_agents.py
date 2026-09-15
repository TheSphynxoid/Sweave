"""Tests for sweave.agents.loader (port of test_agents_loader.py)."""

from __future__ import annotations

from pathlib import Path

from sweave.agents.loader import get_agent_definition, load_seed_agents


EXPECTED_ROLES = {"backend", "frontend", "reviewer", "orchestrator"}


def test_all_four_seed_roles_loaded():
    defs = load_seed_agents()
    assert set(defs) == EXPECTED_ROLES, f"got {sorted(defs)}"


def test_each_role_has_required_fields():
    defs = load_seed_agents()
    for role, d in defs.items():
        assert d.name, f"{role} missing name"
        assert len(d.prompt) > 100, f"{role} prompt too short ({len(d.prompt)} chars)"
        # Step-4 parity flip: seeds default to the native engine
        # (opencode is the per-delegation fallback).
        assert d.harness == "sweave-engine", f"{role} harness {d.harness!r}"
        assert "hindsight_recall" in d.tools, f"{role} missing hindsight_recall"
        # Seeds do NOT pin a model (models.yaml is a catalog; users pick in
        # the UI, per-role defaults were ruled out). model_template must be
        # None — a non-None value here means a broken template snuck back in.
        assert d.model_template is None, (
            f"{role} unexpectedly pins model_template {d.model_template!r}"
        )


def test_unknown_role_returns_none():
    assert get_agent_definition("nonexistent") is None


def test_specialist_seeds_carry_shell_contract():
    """Incident b8544168fa59: 12 min of Unix-on-CMD flailing because
    nothing named the shell. Implementation seeds carry the
    one-line contract (dynamic truth lives in the bash tool
    description; the charter line is static and always true)."""
    defs = load_seed_agents()
    for role in ("backend", "frontend", "reviewer"):
        assert "Match shell syntax to the shell named" in defs[role].prompt, (
            f"{role} seed missing shell contract"
        )


def test_missing_dir_returns_empty():
    # Path that definitely doesn't exist
    result = load_seed_agents(Path("Z:/definitely/missing"))
    assert result == {}


def test_fallback_prompts_intact():
    from sweave.tools import FALLBACK_PROMPTS

    assert len(FALLBACK_PROMPTS) == 4
    assert set(FALLBACK_PROMPTS) == EXPECTED_ROLES

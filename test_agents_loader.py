#!/usr/bin/env python3
"""Test sweave.agents.loader - seed agent definitions (Omnigent-spec YAMLs)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sweave.agents.loader import load_seed_agents, get_agent_definition

EXPECTED_ROLES = {"backend", "frontend", "reviewer", "orchestrator"}


def main() -> int:
    failures = 0

    def check(name, cond, detail=""):
        nonlocal failures
        if not cond:
            failures += 1
        print(f"{'PASS' if cond else 'FAIL'} {name}{(' :: ' + detail) if detail else ''}")

    defs = load_seed_agents()
    check("all four seed roles loaded", set(defs) == EXPECTED_ROLES, str(sorted(defs)))
    for role, d in sorted(defs.items()):
        check(f"{role}: has name", bool(d.name))
        check(f"{role}: has prompt", len(d.prompt) > 100, f"len={len(d.prompt)}")
        check(f"{role}: harness", d.harness == "opencode", d.harness)
        check(f"{role}: hindsight tools", "hindsight_recall" in d.tools)
        check(f"{role}: model template", d.model_template and "{{" in d.model_template)

    check("unknown role -> None", get_agent_definition("nonexistent") is None)

    # Fallback dir: loader must return empty dict, not crash
    check("missing dir -> empty", load_seed_agents(Path("Z:/definitely/missing")) == {})

    # DelegateTaskTool uses YAML prompts via get_agent_definition
    from sweave.tools import DelegateTaskTool, FALLBACK_PROMPTS
    check("fallback prompts intact", len(FALLBACK_PROMPTS) == 4)

    print("ALL GREEN" if failures == 0 else f"RED: {failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

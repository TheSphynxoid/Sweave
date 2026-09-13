"""Engine step 3 tests: build_context() hook (server-side, additive).

Covers the session-stable basics under their frozen ecosystem
standards (sources: docs/CUSTOM_ENGINE_PLAN.md "basics standards"
appendix — AGENTS.md LF standard, SKILL.md agentskills.io spec,
opencode compaction mechanics, opencode todowrite shape):

* Instruction discovery: global -> project -> worktree walk,
  root-down, one file per dir (AGENTS.md wins, CLAUDE.md fallback),
  project==worktree deduped.
* Chain load: blank-joined, empty skipped with reason, 32 KiB cap
  with truncation recorded, unreadable never raises.
* CLAUDE.md @-import resolution (one level, .md only).
* Session cache: injected -> cached -> injected-on-change ->
  injected-after-invalidate; hashes recorded.
* Skills: valid discovered (project shadows global), invalid ->
  skipped rows; L1 index capped; L2 body fetch.
* ContextBuilder: no-budget keeps all + audit shape; over-budget
  drops lowest priority first with a recorded reason.
* Composer integration: new sections ride the body in canonical
  order; default (no files) leaves the body byte-identical to the
  pre-step-3 composition; an explicit total budget drops whole
  sections lowest-priority-first with dropped_* + context_audit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sweave.chat import context as ctx
from sweave.chat.context import (
    PRIORITY_MEMORY,
    PRIORITY_SKILLS,
    ContextBuilder,
    InstructionCache,
    build_context,
    discover_instruction_files,
    discover_skills,
    load_instruction_chain,
    read_skill_body,
    render_skill_index,
)
from sweave.chat.transcript import compose_turn_prompt
from sweave.projects import Session


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _session(sid: str = "s1") -> Session:
    return Session(id=sid, project_name="p", name="n")


# ---------------------------------------------------------------------------
# Instruction discovery + chain
# ---------------------------------------------------------------------------


def test_discovery_order_is_global_project_worktree(tmp_path):
    proj = tmp_path / "proj"
    work = proj / "pkg" / "sub"
    _write(work / "AGENTS.md", "worktree rules")
    _write(proj / "AGENTS.md", "project rules")
    glob = tmp_path / "home"
    _write(glob / "AGENTS.md", "global rules")
    chain = discover_instruction_files(proj, work, global_dir=glob)
    assert chain == [
        glob / "AGENTS.md",
        proj / "AGENTS.md",
        work / "AGENTS.md",
    ]


def test_claude_md_is_fallback_not_peer(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "agents")
    _write(proj / "CLAUDE.md", "claude")
    ghost = tmp_path / "noghome" / "AGENTS.md"
    chain = discover_instruction_files(
        proj, proj, global_dir=tmp_path / "noghome"
    )
    assert chain == [ghost, proj / "AGENTS.md"]
    (proj / "AGENTS.md").unlink()
    chain = discover_instruction_files(
        proj, proj, global_dir=tmp_path / "noghome"
    )
    assert chain == [ghost, proj / "CLAUDE.md"]


def test_project_equals_worktree_dedupes(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "rules")
    chain = discover_instruction_files(
        proj, proj, global_dir=tmp_path / "noghome"
    )
    assert chain == [tmp_path / "noghome" / "AGENTS.md", proj / "AGENTS.md"]


def test_chain_concatenates_root_down_blank_joined(tmp_path):
    a = _write(tmp_path / "a.md", "AAA")
    b = _write(tmp_path / "b.md", "BBB")
    chain = load_instruction_chain([a, b])
    assert chain.text == "AAA\n\nBBB"
    assert all(f["included"] for f in chain.files)
    assert chain.sha != ""


def test_empty_and_missing_files_skipped_with_reason(tmp_path):
    empty = _write(tmp_path / "empty.md", "   \n")
    missing = tmp_path / "gone.md"
    chain = load_instruction_chain([empty, missing])
    assert chain.text == ""
    reasons = {f["path"]: f["reason"] for f in chain.files}
    assert reasons[str(empty)] == "empty"
    assert reasons[str(missing)] == "unreadable"


def test_chain_cap_truncates_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx, "INSTRUCTION_MAX_BYTES", 10)
    big = _write(tmp_path / "big.md", "0123456789ABCDEF")
    chain = load_instruction_chain([big])
    assert chain.truncated is True
    assert len(chain.text.encode("utf-8")) <= 10
    assert chain.files[0]["included"] is True
    assert chain.files[0]["truncated"] is True


def test_claude_import_inlines_agents_md(tmp_path):
    _write(tmp_path / "AGENTS.md", "shared rules")
    _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n\nExtra.")
    chain = load_instruction_chain([tmp_path / "CLAUDE.md"])
    assert "shared rules" in chain.text
    assert "Extra." in chain.text


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------


def test_cache_injected_then_cached_then_change_reinjects(tmp_path):
    proj = tmp_path / "proj"
    agents = _write(proj / "AGENTS.md", "v1")
    cache = InstructionCache()
    first = cache.get("s1", proj, proj, global_dir=tmp_path / "gh")
    assert first.status == "injected"
    assert "v1" in first.text
    second = cache.get("s1", proj, proj, global_dir=tmp_path / "gh")
    assert second.status == "cached"
    assert second.sha == first.sha
    agents.write_text("v2", encoding="utf-8")
    third = cache.get("s1", proj, proj, global_dir=tmp_path / "gh")
    assert third.status == "injected"
    assert "v2" in third.text


def test_cache_invalidate_and_session_isolation(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "rules")
    cache = InstructionCache()
    assert cache.get("s1", proj, proj).status == "injected"
    assert cache.get("s1", proj, proj).status == "cached"
    assert cache.get("s2", proj, proj).status == "injected"
    cache.invalidate("s1")
    assert cache.get("s1", proj, proj).status == "injected"


# ---------------------------------------------------------------------------
# Skills (SKILL.md spec)
# ---------------------------------------------------------------------------

_VALID_SKILL = """---
name: plan
description: Persist plans across sessions. Use when planning work.
---

# Plan skill
"""


def _skill_body(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name} skill\n"


def _skill(root: Path, name: str, body: str | None = None) -> Path:
    if body is None:
        body = _skill_body(name, f"Skill {name} does things. Use when testing.")
    return _write(root / "skills" / name / "SKILL.md", body)


def test_valid_skill_discovered_project_shadows_global(tmp_path):
    proj = tmp_path / "proj"
    glob = tmp_path / "gh"
    _skill(proj, "plan")
    _skill(glob, "plan", _VALID_SKILL.replace("Persist plans", "Global plans"))
    _skill(glob, "tickets", _skill_body("tickets", "Track tickets. Use when triaging."))
    skills, skipped = discover_skills(proj, global_dir=glob)
    by_name = {s.name: s for s in skills}
    assert set(by_name) == {"plan", "tickets"}
    assert by_name["plan"].source == "project"
    assert by_name["tickets"].source == "global"
    assert any(s["reason"].startswith("shadowed") for s in skipped)


@pytest.mark.parametrize(
    "name,body_reason",
    [
        ("BadName", "invalid name"),
        ("-lead", "invalid name"),
        ("a" * 65, "invalid name"),
        ("ok", "missing required frontmatter: description"),
    ],
)
def test_invalid_skills_skipped_with_reason(tmp_path, name, body_reason):
    proj = tmp_path / "proj"
    if "description" in body_reason:
        body = "---\nname: ok\n---\n\nBody.\n"
    else:
        body = f"---\nname: {name}\ndescription: D.\n---\n\nBody.\n"
    _write(proj / "skills" / "whatever" / "SKILL.md", body)
    skills, skipped = discover_skills(proj, global_dir=tmp_path / "gh")
    assert skills == []
    assert len(skipped) == 1
    assert body_reason in skipped[0]["reason"]


def test_skill_name_must_match_directory(tmp_path):
    proj = tmp_path / "proj"
    _write(
        proj / "skills" / "other" / "SKILL.md",
        "---\nname: plan\ndescription: D.\n---\n",
    )
    skills, skipped = discover_skills(proj, global_dir=tmp_path / "gh")
    assert skills == []
    assert "!= directory" in skipped[0]["reason"]


def test_skill_index_capped_with_drops(tmp_path):
    proj = tmp_path / "proj"
    for i in range(5):
        _skill(proj, f"skill-{i}")
    skills, _ = discover_skills(proj, global_dir=tmp_path / "gh")
    text, dropped = render_skill_index(skills, budget=40)
    assert text.startswith("## Skills")
    assert dropped  # over the tiny budget, some skills drop by name
    assert all(isinstance(name, str) for name in dropped)


def test_read_skill_body_l2(tmp_path):
    proj = tmp_path / "proj"
    _skill(proj, "plan")
    body = read_skill_body("plan", proj, global_dir=tmp_path / "gh")
    assert body is not None and "# plan skill" in body
    assert "---" not in body.splitlines()[0]
    assert read_skill_body("nope", proj, global_dir=tmp_path / "gh") is None


# ---------------------------------------------------------------------------
# ContextBuilder budget + audit
# ---------------------------------------------------------------------------


def test_builder_no_budget_keeps_all_with_audit():
    builder = ContextBuilder()
    builder.add("memory", "m", PRIORITY_MEMORY)
    builder.add("skills", "s", PRIORITY_SKILLS)
    result = builder.finalize()
    assert result.kept == ["memory", "skills"]
    assert result.dropped == []
    assert result.audit["sections"] == {"memory": 1, "skills": 1}
    assert result.audit["tokens"] == 2
    assert result.text == "m\n\ns"


def test_builder_over_budget_drops_lowest_priority_first():
    builder = ContextBuilder()
    builder.add("memory", "memory text here", PRIORITY_MEMORY)
    builder.add("skills", "skills text here", PRIORITY_SKILLS)
    # Each line is 3 words -> 4 tokens; total 8. Budget 5 drops the
    # lower-priority skills section first and then fits.
    result = builder.finalize(total_budget=5)
    assert result.kept == ["memory"]
    assert result.dropped == [
        {"section": "skills", "reason": "over total budget"}
    ]
    assert "skills" not in result.audit["sections"]


def test_builder_skips_empty_sections_silently():
    builder = ContextBuilder()
    builder.add("memory", "", PRIORITY_MEMORY)
    result = builder.finalize()
    assert result.kept == []
    assert result.text == ""


def test_build_context_merges_stable_and_extra(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "Be kind.")
    _skill(proj, "plan")
    text, audit, snapshot = build_context(
        session_key="s1",
        project_dir=proj,
        extra_sections=[("memory", "recall this", PRIORITY_MEMORY)],
        global_dir=tmp_path / "gh",
    )
    assert "Be kind." in text
    assert "plan" in text
    assert "recall this" in text
    assert audit["instructions_status"] == "injected"
    assert audit["instructions_sha"] == snapshot.sha
    assert set(audit["sections"]) == {"instructions", "skills", "memory"}


# ---------------------------------------------------------------------------
# Composer integration
# ---------------------------------------------------------------------------


async def _compose(tmp_path, **kwargs):
    return await compose_turn_prompt(
        session=_session(),
        user_message="do it",
        project_dir=tmp_path / "proj",
        memory_bank_id=None,
        memory_backend=None,
        git_snapshotter=None,
        children=None,
        transcript_messages=None,
        context_cache=InstructionCache(),
        **kwargs,
    )


def test_composer_default_body_unchanged_without_files(tmp_path):
    import asyncio

    (tmp_path / "proj").mkdir()
    composed = asyncio.run(_compose(tmp_path))
    assert composed.instructions_section == ""
    assert composed.skills_section == ""
    assert composed.to_body() == "do it"
    assert composed.context_audit is not None
    assert composed.context_audit["dropped"] == []


def test_composer_sections_ride_body_in_canonical_order(tmp_path):
    import asyncio

    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "INSTRUCTIONS")
    _skill(proj, "plan")
    composed = asyncio.run(_compose(tmp_path))
    body = composed.to_body()
    assert body.index("INSTRUCTIONS") < body.index("plan")
    assert body.index("plan") < body.index("do it")


def test_composer_total_budget_drops_whole_section(tmp_path):
    import asyncio

    proj = tmp_path / "proj"
    _write(proj / "AGENTS.md", "INSTRUCTIONS " * 50)
    composed = asyncio.run(_compose(tmp_path, context_budget=1))
    # Everything drops at budget 1 except nothing fits: user message
    # is not budgeted (unbounded by design — the request always rides).
    assert composed.to_body() == "do it"
    assert composed.dropped_instructions == ["over total budget"]
    assert composed.context_audit is not None
    sections = {d["section"] for d in composed.context_audit["dropped"]}
    assert "instructions" in sections


def test_composer_explicit_texts_skip_loading(tmp_path):
    import asyncio

    composed = asyncio.run(
        _compose(
            tmp_path,
            instructions_text="EXPLICIT",
            skills_text="",
            worktree_dir=None,
        )
    )
    assert composed.instructions_section == "EXPLICIT"
    assert composed.context_audit["instructions_status"] == "provided"

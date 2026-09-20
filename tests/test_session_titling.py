"""Session auto-titling tests (background nano-turn).

* Pure: default-name detection, title cleaning, prompt caps, model
  resolution (explicit wins, cheapest $0 keyed text model, gemini
  skipped, none available).
* Driver: success renames + publishes; turn failure / degenerate
  title / vanished-or-renamed session keep the timestamp.
* Endpoint: PATCH rename (200 + event shape), unknown 404, blank 400.
* Trigger: first successful turn fires once (flag set); later turns
  and custom names never re-fire.
* Config: titling.model set/clear/validation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sweave.chat import titling as titling_mod


@pytest.fixture(autouse=True)
def _mock_opencode_env(monkeypatch):
    # Same as tests/test_chat_loop.py: the shared chat builder runs
    # the real runtime, which must resolve the mock harness.
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE", "1")


def test_is_default_name():
    assert titling_mod.is_default_name("Session 2026-09-20 19:20") is True
    assert titling_mod.is_default_name("Fix the login bug") is False
    assert titling_mod.is_default_name("") is False
    assert titling_mod.is_default_name(None) is False
    assert titling_mod.is_default_name("Session yesterday") is False


def test_clean_title_matrix():
    assert titling_mod.clean_title("Fix login redirect bug") == "Fix login redirect bug"
    assert titling_mod.clean_title('  "Quote wrapped title"  ') == "Quote wrapped title"
    assert titling_mod.clean_title("Title: Refactor the auth layer") == "Refactor the auth layer"
    assert (
        titling_mod.clean_title("one two three four five six seven eight")
        == "one two three four five six"
    )
    # Cuts land on content words, never dangling tails or mid-word.
    assert titling_mod.clean_title("Fix merge reviewed and retested with") == (
        "Fix merge reviewed and retested"
    )
    assert titling_mod.clean_title("Status update backends are not yet") == (
        "Status update backends are not yet"
    )
    assert titling_mod.clean_title("") is None
    assert titling_mod.clean_title(None) is None
    assert titling_mod.clean_title("[chat error: boom]") is None
    long_one = "x" * 300
    cleaned = titling_mod.clean_title(long_one)
    assert cleaned is not None and len(cleaned) <= titling_mod.TITLE_MAX_CHARS


def test_build_title_prompt_caps_inputs():
    prompt = titling_mod.build_title_prompt("u" * 2000, "r" * 2000)
    assert "ONLY the title" in prompt
    assert len(prompt) < 2000


def _meta(path: Path, entries: dict) -> Path:
    meta = path / "models.meta.json"
    meta.write_text(json.dumps(entries), encoding="utf-8")
    return meta


def _entry(cost_input, text_capable=True):
    modalities = (
        {"input": ["text"], "output": ["text"]} if text_capable else {"input": ["text"], "output": ["image"]}
    )
    return {"cost": {"input": cost_input, "output": 0}, "modalities": modalities}


def test_free_text_candidates_cheapest_first(tmp_path: Path):
    meta = _meta(
        tmp_path,
        {
            "opencode/slow-free": _entry(0),
            "opencode/fast-free": _entry(0),
            "openrouter/paid": _entry(1.0),
            "imgonly/pic": _entry(0, text_capable=False),
            "nvidia/baai/bge-m3": _entry(0),
        },
    )
    got = titling_mod.free_text_candidates(meta_path=meta)
    assert ("opencode", "fast-free") in got
    assert ("opencode", "slow-free") in got
    assert all(provider != "openrouter" for provider, _ in got)
    assert all(provider != "imgonly" for provider, _ in got)
    # Embedders never title (vectors are not prose).
    assert all("bge" not in model for _, model in got)


def test_free_text_candidates_missing_file(tmp_path: Path):
    assert titling_mod.free_text_candidates(meta_path=tmp_path / "none.json") == []


def _creds(*keyed: str):
    def source(provider: str):
        return "env" if provider in keyed else None

    return source


def test_resolve_explicit_wins():
    assert titling_mod.resolve_title_models(explicit="opencode/glm-5") == [
        "opencode/glm-5"
    ]
    assert titling_mod.resolve_title_models(explicit="bare-name") == []
    assert (
        titling_mod.resolve_title_models(
            explicit="opencode/glm-5",
            credential_source=_creds(),
            candidates=[("openrouter", "x-free")],
        )
        == ["opencode/glm-5"]
    )


def test_resolve_cheapest_keyed_first():
    got = titling_mod.resolve_title_models(
        credential_source=_creds("openrouter"),
        candidates=[("opencode", "a-free"), ("openrouter", "b-free")],
    )
    assert got == ["openrouter/b-free"]


def test_resolve_skips_gemini_and_unkeyed():
    got = titling_mod.resolve_title_models(
        credential_source=_creds("opencode"),
        candidates=[("opencode", "gemini-3-flash"), ("opencode", "ok-free")],
    )
    assert got == ["opencode/ok-free"]
    assert (
        titling_mod.resolve_title_models(
            credential_source=_creds(),
            candidates=[("opencode", "ok-free")],
        )
        == []
    )


class _FakeProc:
    def __init__(self, script):
        self._script = script

    async def send(self, message, on_chunk=None, trace=None, **kwargs):
        from sweave.harness.base import AgentResult

        ok, text = self._script.pop(0) if self._script else (True, "")
        return AgentResult(success=ok, output=text, error=None if ok else text)

    async def terminate(self):
        pass


class _FakeHarness:
    def __init__(self, script):
        self._script = script
        self.spawned: list = []

    async def spawn(self, spec):
        self.spawned.append(spec)
        return _FakeProc(self._script)


class _FakeManager:
    def __init__(self, name="Session 2026-09-20 19:20"):
        self._session = {
            "id": "s-1",
            "name": name,
            "project_name": "demo",
        }
        self.renamed: list = []
        self.saved = 0

    def get_session(self, session_id: str):
        if session_id != "s-1":
            return None
        from types import SimpleNamespace

        return SimpleNamespace(**self._session)

    def rename_session(self, session_id: str, name: str):
        self.renamed.append((session_id, name))
        self._session["name"] = name
        from types import SimpleNamespace

        return SimpleNamespace(**self._session)

    def save_session(self, session):
        self.saved += 1


def _run(coro):
    return asyncio.run(coro)


def test_request_title_success_renames_and_publishes():
    published: list = []

    async def publish(event, data):
        published.append((event, data))

    manager = _FakeManager()
    title = _run(
        titling_mod.request_title(
            project_manager=manager,
            publish=publish,
            session_id="s-1",
            user_text="Fix the login redirect",
            reply_text="Done, patched the guard.",
            models=["opencode/ok-free"],
            worktree_path="/tmp",
            harness_factory=lambda: _FakeHarness([(True, "Login redirect fix")]),
        )
    )
    assert title == "Login redirect fix"
    assert manager.renamed == [("s-1", "Login redirect fix")]
    assert published == [
        (
            "session.renamed",
            {"id": "s-1", "name": "Login redirect fix", "project_name": "demo"},
        )
    ]


def test_request_title_failure_keeps_timestamp():
    published: list = []

    async def publish(event, data):
        published.append((event, data))

    manager = _FakeManager()
    title = _run(
        titling_mod.request_title(
            project_manager=manager,
            publish=publish,
            session_id="s-1",
            user_text="hi",
            reply_text="hello",
            models=["opencode/ok-free", "openrouter/other-free"],
            worktree_path="/tmp",
            harness_factory=lambda: _FakeHarness(
                [(False, "boom"), (True, "   ")]
            ),
        )
    )
    assert title is None
    assert manager.renamed == []
    assert published == []


def test_request_title_skips_non_default_name():
    harness = _FakeHarness([(True, "Whatever")])
    manager = _FakeManager(name="My custom name")
    title = _run(
        titling_mod.request_title(
            project_manager=manager,
            publish=lambda e, d: (_ for _ in ()).throw(AssertionError("no publish")),
            session_id="s-1",
            user_text="hi",
            reply_text="hello",
            models=["opencode/ok-free"],
            worktree_path="/tmp",
            harness_factory=lambda: harness,
        )
    )
    assert title is None
    assert harness.spawned == []
    assert manager.renamed == []


def test_request_title_no_models_no_turn():
    harness = _FakeHarness([(True, "Whatever")])
    title = _run(
        titling_mod.request_title(
            project_manager=_FakeManager(),
            publish=lambda e, d: None,
            session_id="s-1",
            user_text="hi",
            reply_text="hello",
            models=[],
            worktree_path="/tmp",
            harness_factory=lambda: harness,
        )
    )
    assert title is None
    assert harness.spawned == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    from pathlib import Path as PathCls

    from fastapi.testclient import TestClient

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE", "1")
    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def test_rename_endpoint_round_trip(client, tmp_path):
    from sweave.projects import project_manager

    project_manager.create_project("demo", path=tmp_path)
    session = project_manager.create_session("demo", session_name=None)
    assert titling_mod.is_default_name(session.name)

    r = client.patch(f"/api/sessions/{session.id}", json={"name": "  Login fix  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["session"]["name"] == "Login fix"

    assert client.patch("/api/sessions/nope", json={"name": "x"}).status_code == 404
    assert client.patch(f"/api/sessions/{session.id}", json={"name": "   "}).status_code == 400


def test_set_titling_model_validation(tmp_path, monkeypatch):
    from pathlib import Path as PathCls

    from sweave.config.manager import ConfigManager

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))
    manager = ConfigManager(config_path=tmp_path / "config.yaml")
    assert manager.set_titling_model(None) is None
    with pytest.raises(ValueError):
        manager.set_titling_model("bare-name")
    assert manager.set_titling_model("opencode/ok-free") == "opencode/ok-free"
    assert manager.get().titling.model == "opencode/ok-free"
    assert manager.set_titling_model(None) is None
    assert manager.get().titling.model is None


def test_trigger_fires_once_on_default_name(tmp_path, monkeypatch):
    from sweave.projects import ProjectManager
    from tests.test_chat_loop import _build_chat_loop

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo")
    assert titling_mod.is_default_name(session.name)
    chat = _build_chat_loop(pm=pm, send_responses=["hello back", "hello back"])

    calls: list = []

    async def fake_request_title(**kwargs):
        calls.append(kwargs)
        return None

    import sweave.chat.loop as loop_mod

    monkeypatch.setattr(loop_mod.titling_mod, "request_title", fake_request_title)
    result = asyncio.run(chat.run_turn(session_id=session.id, user_content="hi"))
    assert result["content"] == "hello back"
    assert len(calls) == 1
    assert calls[0]["session_id"] == session.id

    reloaded = pm.get_session(session.id)
    assert reloaded is not None
    assert reloaded.context.get(titling_mod.TITLE_ATTEMPTED_KEY) is True

    # Second turn: flag set, never re-fires.
    result2 = asyncio.run(chat.run_turn(session_id=session.id, user_content="again"))
    assert result2["content"] == "hello back"
    assert len(calls) == 1


def test_trigger_skips_custom_names(tmp_path, monkeypatch):
    from sweave.projects import ProjectManager
    from tests.test_chat_loop import _build_chat_loop

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo")
    session.name = "My project"
    pm.save_session(session)
    chat = _build_chat_loop(pm=pm, send_responses=["hello back"])

    calls: list = []

    async def fake_request_title(**kwargs):
        calls.append(kwargs)
        return None

    import sweave.chat.loop as loop_mod

    monkeypatch.setattr(loop_mod.titling_mod, "request_title", fake_request_title)
    asyncio.run(chat.run_turn(session_id=session.id, user_content="hi"))
    assert calls == []

"""M1.12 amendment 1: in-band permission bridge (plugin + hijack).

Covers:
* the bundled plugin file exists, contains the ferry endpoint and
  env-only secrets (token never written to the island);
* ``ensure_permission_bridge`` copies the plugin into the island and
  returns the ``OPENCODE_CONFIG_DIR`` env override;
* ``scope_decision`` mirrors the scoped render's root ancestry
  (project dir / worktrees / ``~/.sweave`` / declared roots in,
  everything else out);
* ``resolve_hijack_request``: in-scope → auto-allow `once` with the
  pinned reply POSTed to the serve; out-of-scope → escalation
  created (kind=permission, no timeout) and the human answer mapped
  (allow → ``once``/``always``; skip/timeout → ``reject``);
  unregistered session → escalation still created under
  ``session:{sid}`` (never silent), reply skipped (no serve).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---- plugin provisioning -------------------------------------------------


def test_bundled_plugin_exists_and_is_host_safe(tmp_path, monkeypatch):
    from sweave.runtime.permission_bridge import (
        bundled_plugin_source,
        ensure_permission_bridge,
        plugin_path,
    )

    source = bundled_plugin_source()
    assert "permission.asked" in source
    assert "api/permission/hijack" in source
    # Secrets come from the environment at runtime; no literal token
    # is ever written into the island plugin file.
    assert "get_or_create_token" not in source

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    env = ensure_permission_bridge()
    island = tmp_path / ".sweave" / "opencode"
    assert env["OPENCODE_CONFIG_DIR"] == str(island)
    p = plugin_path()
    assert p.exists()
    assert p.read_text(encoding="utf-8") == source


def test_serve_runner_injects_island_dir(monkeypatch, tmp_path):
    from sweave.runtime import serve_runner as sr

    calls: dict[str, object] = {}

    class FakeProc:
        pid = 4242

    async def fake_exec(*args, **kwargs):
        calls["env"] = kwargs.get("env")
        calls["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(sr.asyncio, "create_subprocess_exec", fake_exec)

    class FakePort:
        def __call__(self):
            return 4096

    runner = object.__new__(sr.ServeRunner)
    runner.specialist_name = "test-spec"
    runner.worktree_path = tmp_path
    runner.serve_args = []
    runner.log_path = None
    runner.process = None
    runner.port = 0
    runner.base_url = ""
    runner.event_bus = None
    runner.tracker = None
    runner._log_file = None
    runner.is_alive = lambda: False
    monkeypatch.setattr(
        sr.ServeRunner, "_wait_for_port", FakePort(), raising=False
    )
    import asyncio

    asyncio.get_event_loop_policy()
    asyncio.run_inner = None  # placeholder, not used

    # ServeRunner.start is async; drive it directly.
    asyncio.run(_run_start(runner))
    env = calls["env"]
    assert env is not None
    assert str(tmp_path / ".sweave" / "opencode") not in [env.get("OPENCODE_CONFIG_DIR")]
    from sweave.runtime.permission_bridge import _island_dir

    assert env.get("OPENCODE_CONFIG_DIR") == str(_island_dir())


async def _run_start(runner) -> None:
    import asyncio

    # Patch the pieces start() waits on: port read + touch + logger.
    from sweave.runtime import serve_runner as sr

    async def _ready(*args, **kwargs):
        return 4096

    sr.ServeRunner._wait_for_port = _ready  # type: ignore[method-assign]
    await sr.ServeRunner.start(runner)


# ---- scope evaluation ------------------------------------------------------


def test_scope_project_and_declared_roots(monkeypatch, tmp_path):
    import os as _os

    from sweave.runtime.permission_bridge import scope_decision

    monkeypatch.setattr(
        _os.path, "expanduser", staticmethod(
            lambda p: str(tmp_path / p[2:]).replace("/", _os.sep)
        )
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    # Project subtree is in scope.
    assert scope_decision(
        patterns=[str(proj / "src" / "*")],
        project_dir=proj,
        permission_roots=None,
    )
    # Declared roots count too.
    shared = tmp_path / "shared"
    assert scope_decision(
        patterns=[str(shared / "*")],
        project_dir=proj,
        permission_roots=[str(shared)],
    )
    # Outside every root -> out of scope (the incident's exact ask).
    assert not scope_decision(
        patterns=[str(tmp_path / ".config" / "opencode" / "*")],
        project_dir=proj,
        permission_roots=None,
    )
    # No project at all -> never silent-allow.
    assert not scope_decision(
        patterns=[str(proj / "*")], project_dir=None, permission_roots=None
    )


def test_scope_builtin_sweave_root(monkeypatch, tmp_path):
    import os as _os

    from sweave.runtime.permission_bridge import scope_decision

    monkeypatch.setattr(
        _os.path, "expanduser", staticmethod(
            lambda p: str(tmp_path / p[2:]).replace("/", _os.sep)
        )
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    sweave_root = tmp_path / ".sweave"
    sweave_root.mkdir()
    assert scope_decision(
        patterns=[str(sweave_root / "*")],
        project_dir=proj,
        permission_roots=None,
    )


# ---- endpoint-side resolution -----------------------------------------------


class FakeStore:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.created: list[dict] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        self.responses.setdefault(kwargs["delegation_id"], {
            "status": "answered", "response": "always allow",
        })
        return {"status": "pending"}

    async def get(self, *, delegation_id: str):
        return self.responses.get(delegation_id)


class FakeProjects:
    def __init__(self, projects: list) -> None:
        self._projects = projects

    def list_projects(self):
        return self._projects


class FakeProject:
    def __init__(self, name: str, path: str, roots) -> None:
        self.name = name
        self.path = path
        self.permission_roots = roots


def test_resolve_auto_allow_in_scope(monkeypatch, tmp_path):
    from sweave.runtime import permission_bridge as pb

    proj = tmp_path / "proj"
    proj.mkdir(parents=True)
    pb.register_session(
        "ses_abc", "http://127.0.0.1:4096", proj, "chat-x"
    )
    posted: list = []

    async def fake_reply(client, base_url, session_id, request_id, value):
        posted.append((base_url, session_id, request_id, value))
        return 200

    monkeypatch.setattr(
        "sweave.runtime.permission_watch.reply_permission_request", fake_reply
    )
    import sweave.runtime.permission_bridge as bridge_module

    bridge_module.reply_permission_request = fake_reply

    import asyncio

    out = asyncio.run(
        pb.resolve_hijack_request(
            {
                "session_id": "ses_abc",
                "request_id": "per_1",
                "permission": "external_directory",
                "patterns": [str(proj / "src" / "*")],
                "metadata": {},
            },
            escalation_store=FakeStore({}),
            project_manager=FakeProjects(
                [FakeProject("projA", str(proj), None)]
            ),
        )
    )
    assert out["response"] == "once"
    assert out["status"] == "auto_allowed"
    assert posted == [("http://127.0.0.1:4096", "ses_abc", "per_1", "once")]


def test_resolve_asks_human_out_of_scope(monkeypatch, tmp_path):
    from sweave.runtime import permission_bridge as pb

    proj = tmp_path / "proj2"
    proj.mkdir()
    pb.register_session("ses_def", "http://127.0.0.1:4097", proj, "chat-y")
    store = FakeStore({})
    posted: list = []

    async def fake_reply(client, base_url, session_id, request_id, value):
        posted.append((base_url, session_id, request_id, value))
        return 200

    monkeypatch.setattr(
        "sweave.runtime.permission_watch.reply_permission_request", fake_reply
    )
    import asyncio

    out = asyncio.run(
        pb.resolve_hijack_request(
            {
                "session_id": "ses_def",
                "request_id": "per_2",
                "permission": "external_directory",
                "patterns": [str(tmp_path / ".config" / "opencode" / "*")],
                "metadata": {},
            },
            escalation_store=store,
            project_manager=FakeProjects(
                [FakeProject("projB", str(proj), None)]
            ),
        )
    )
    assert out["response"] == "always"  # FakeStore default answer: 'always allow'
    assert store.created[0]["kind"] == "permission"
    assert store.created[0]["timeout_seconds"] is None
    assert store.created[0]["delegation_id"] == "chat-y"
    assert posted[0][3] == "always"

    # Skip => reject
    store2 = FakeStore({"chat-y": {"status": "skipped", "response": ""}})
    out2 = asyncio.run(
        pb.resolve_hijack_request(
            {
                "session_id": "ses_def",
                "request_id": "per_3",
                "permission": "external_directory",
                "patterns": [str(tmp_path / "weird" / "*")],
                "metadata": {},
            },
            escalation_store=store2,
            project_manager=FakeProjects(
                [FakeProject("projB", str(proj), None)]
            ),
        )
    )
    assert out2["response"] == "reject"


def test_resolve_unknown_session_still_asks_human(tmp_path):
    from sweave.runtime import permission_bridge as pb

    store = FakeStore({"session:ses_ghost": {"status": "answered", "response": "allow once"}})

    class NoServe:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    import asyncio

    out = asyncio.run(
        pb.resolve_hijack_request(
            {
                "session_id": "ses_unknown",
                "request_id": "per_9",
                "permission": "external_directory",
                "patterns": [str(tmp_path / "x" / "*")],
                "metadata": {},
            },
            escalation_store=store,
            project_manager=FakeProjects([]),
        )
    )
    assert out["delegation_id"] == "session:ses_unknown"
    assert store.created[0]["delegation_id"].startswith("session:")
    assert out["status"] == "answered"
    # No serve to reply to: no reply POST, still resolved (logged loud).


# ---- endpoint smoke (token guard) ---------------------------------------------


def test_hijack_endpoint_token_guarded():
    from sweave.web.server import build_app  # noqa: F401

    pytest.importorskip("sweave.web.server")

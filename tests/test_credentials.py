"""Credential-ownership tests (user ruling 2026-09-14).

Sweave owns keys in ~/.sweave/credentials.json (0600); the opencode
auth store is an import source (adopt once, drift prompts,
reverse-sync ours→theirs). Hermetic: tmp home + explicit store
paths throughout — the real stores are never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweave.credentials import (
    CredentialStore,
    credential_source,
    fingerprint_key,
    opencode_auth_paths,
    read_opencode_store,
    sync_with_opencode,
)


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(path=tmp_path / "credentials.json")


def _opencode_home(tmp_path: Path, entries: dict) -> Path:
    """Plant an isolated-copy opencode store under a fake home."""
    home = tmp_path / "home"
    target = home / ".sweave" / "opencode-data" / "opencode" / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries), encoding="utf-8")
    return home


def test_set_get_delete_round_trip(tmp_path: Path):
    store = _store(tmp_path)
    assert store.get_key("openrouter") is None
    store.set_api_key("openrouter", "sk-test-4242", source="manual")
    assert store.get_key("openrouter") == "sk-test-4242"
    # Reload from disk: durable.
    assert _store(tmp_path).get_key("openrouter") == "sk-test-4242"
    assert store.delete("openrouter") is True
    assert store.get_key("openrouter") is None
    assert store.delete("openrouter") is False


def test_set_rejects_empty_key(tmp_path: Path):
    with pytest.raises(ValueError):
        _store(tmp_path).set_api_key("openrouter", "   ")


def test_public_view_never_carries_secrets(tmp_path: Path):
    store = _store(tmp_path)
    entry = store.set_api_key("openrouter", "sk-live-secret-9999")
    assert "sk-live-secret-9999" not in json.dumps(
        store.list_public()
    )
    assert entry["key_suffix"] == "9999"
    assert entry["fingerprint"] == fingerprint_key("sk-live-secret-9999")
    assert entry["source"] == "manual"


def test_fingerprint_stable():
    assert fingerprint_key("abc") == fingerprint_key("abc")
    assert fingerprint_key("abc") != fingerprint_key("abd")


def test_adopt_keys_skips_oauth_and_empties(tmp_path: Path):
    store = _store(tmp_path)
    result = store.adopt(
        {
            "openrouter": {"key": "sk-a", "type": "api"},
            "github-copilot": {
                "type": "oauth",
                "access": "tok",
                "refresh": "ref",
                "expires": 999,
            },
            "empty": {"key": "", "type": "api"},
            "weird": [1, 2],
        }
    )
    assert result["adopted"] == ["openrouter"]
    assert store.get_key("openrouter") == "sk-a"
    reasons = {s["provider"]: s["reason"] for s in result["skipped"]}
    assert "oauth" in reasons["github-copilot"]
    assert reasons["empty"] == "no key material"
    # OAuth is detect-only: marker recorded, secrets never copied.
    marker = store.load()["providers"]["github-copilot"]
    assert marker["type"] == "oauth" and marker["adopted"] is False
    assert "access" not in json.dumps(store.load())


def test_adopt_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    first = store.adopt({"openrouter": {"key": "sk-a", "type": "api"}})
    second = store.adopt({"openrouter": {"key": "sk-a", "type": "api"}})
    assert first["adopted"] == ["openrouter"]
    assert second["adopted"] == []


def test_adopt_never_clobbers_ours_force_import_does(tmp_path: Path):
    store = _store(tmp_path)
    store.adopt({"openrouter": {"key": "sk-v1", "type": "api"}})
    store.set_api_key("openrouter", "sk-v2-rotated", source="manual")
    # Their stale v1 must not overwrite our rotation…
    result = store.adopt({"openrouter": {"key": "sk-v1", "type": "api"}})
    assert result["adopted"] == []
    assert store.get_key("openrouter") == "sk-v2-rotated"
    # …but an explicit user-approved import does.
    forced = store.adopt(
        {"openrouter": {"key": "sk-v1", "type": "api"}}, force=True
    )
    assert forced["adopted"] == ["openrouter"]
    assert store.get_key("openrouter") == "sk-v1"


def test_pending_imports_new_rotated_and_pushed(tmp_path: Path):
    store = _store(tmp_path)
    remote = {"openrouter": {"key": "sk-a", "type": "api"}}
    assert store.pending_imports(remote) == [
        {"provider": "openrouter", "reason": "new in opencode"}
    ]
    store.adopt(remote)
    assert store.pending_imports(remote) == []
    rotated = {"openrouter": {"key": "sk-B", "type": "api"}}
    assert store.pending_imports(rotated) == [
        {"provider": "openrouter", "reason": "rotated in opencode"}
    ]
    # Ours, pushed there: same fingerprint → not pending.
    store.set_api_key("zai", "zk-1", source="manual")
    assert store.pending_imports(
        {"zai": {"key": "zk-1", "type": "api"}}
    ) == []


def test_push_candidates_and_render(tmp_path: Path):
    store = _store(tmp_path)
    store.set_api_key("openrouter", "sk-a", source="manual")
    store.load()["providers"]["github-copilot"] = {
        "type": "oauth",
        "adopted": False,
    }
    assert store.push_candidates({}) == ["openrouter"]
    assert store.push_candidates(
        {"openrouter": {"key": "sk-a", "type": "api"}}
    ) == []
    assert store.push_candidates(
        {"openrouter": {"key": "sk-OTHER", "type": "api"}}
    ) == ["openrouter"]
    rendered = store.render_push_entry("openrouter")
    assert rendered == {"key": "sk-a", "type": "api"}
    assert store.render_push_entry("github-copilot") is None
    assert store.render_push_entry("missing") is None


def test_sync_end_to_end_with_backup(tmp_path: Path, monkeypatch):
    home = _opencode_home(
        tmp_path,
        {
            "openrouter": {"key": "sk-a", "type": "api"},
            "github-copilot": {"type": "oauth", "access": "t"},
        },
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    store = CredentialStore()  # home-anchored, lands in tmp
    result = sync_with_opencode(store)
    assert result["adopted"] == ["openrouter"]
    assert any(s["provider"] == "github-copilot" for s in result["skipped"])
    assert store.get_key("openrouter") == "sk-a"
    assert (home / ".sweave" / "credentials.json").is_file()
    # Reverse-sync: add ours, re-sync → pushed + backup of theirs kept.
    store.set_api_key("zai", "zk-1", source="manual")
    result2 = sync_with_opencode(store)
    pushed_paths = [p for paths in result2["pushed"].values() for p in paths]
    assert pushed_paths, "expected a push on second sync"
    target = home / ".sweave" / "opencode-data" / "opencode" / "auth.json"
    assert json.loads(target.read_text(encoding="utf-8"))["zai"] == {
        "key": "zk-1",
        "type": "api",
    }
    assert target.with_name(target.name + ".sweave-bak").is_file()
    # Third sync: converged — nothing pending, nothing pushed.
    result3 = sync_with_opencode(store)
    assert result3["adopted"] == [] and result3["pending"] == []
    assert result3["pushed"] == {}


def test_credential_source_tiers(tmp_path: Path, monkeypatch):
    home = _opencode_home(tmp_path, {"openrouter": {"key": "sk-a", "type": "api"}})
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    for var in (
        "SWEAVE_ENGINE_KEY_OPENROUTER",
        "OPENROUTER_API_KEY",
        "SWEAVE_ENGINE_KEY_ZAI",
        "ZAI_API_KEY",
        "Z_AI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    store = CredentialStore()
    assert credential_source("openrouter", store) == "opencode-legacy"
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert credential_source("openrouter", store) == "env"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    sync_with_opencode(store)
    assert credential_source("openrouter", store) == "sweave"
    assert credential_source("never-heard-of-it", store) is None


def test_router_paths_registered():
    from sweave.web.routers.credentials import router

    paths = sorted(
        {getattr(r, "path", "") for r in router.routes}
    )
    assert "/api/providers" in paths
    assert "/api/credentials/pending" in paths
    assert "/api/credentials/import" in paths
    assert "/api/credentials" in paths
    assert "/api/credentials/{provider}" in paths


def test_server_includes_credentials_router():
    from sweave.web.server import app

    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path:
            paths.add(path)
        # Included APIRouters mount as pathless _IncludedRouter
        # records — the paths live on original_router.routes.
        inner = getattr(route, "original_router", None)
        for sub in getattr(inner, "routes", []) or []:
            sub_path = getattr(sub, "path", "")
            if sub_path:
                paths.add(sub_path)
    assert "/api/providers" in paths
    assert "/api/v2/tasks" in paths  # mount-flattening sanity


def test_opencode_paths_order_and_read_missing(tmp_path: Path):
    paths = opencode_auth_paths(home=tmp_path / "nobody")
    assert paths[0].name == "auth.json"
    assert ".sweave" in paths[0].parts
    assert read_opencode_store(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("not json{{", encoding="utf-8")
    assert read_opencode_store(tmp_path / "bad.json") == {}

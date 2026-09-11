"""User default out of models.yaml (fast-track 2026-09-11).

Three writers shared one file: ``set_default_model`` persisted the
user's selection INTO models.yaml, ``sync_registry`` read
``old_default`` from the same file and rewrote it, and any stale read
silently replaced the selection. Generated artifact + user state in
one file was the bug.

New home: config.yaml ``models.default``. These tests pin the plan
gates — set→sync→survives, legacy adoption iff-config-unset, sync
output carries no ``default`` key, and the parallel-writer scenario.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import sweave.models_sync as models_sync_mod
import sweave.web.routers.config as config_router
from sweave.config.manager import ConfigManager, _set_models_default_line
from sweave.models_sync import write_registry


def _write(tmp_path: Path, *, registry_default: str | None = None,
           config_default: str | None = None,
           customs_default: str | None = None) -> Path:
    """Plant a tmp config + registry pair. Returns the config path."""
    providers = {"opencode": ["a-model"], "ollama": ["b-model"]}
    reg_doc: dict = {"models": {"providers": providers}}
    if registry_default is not None:
        reg_doc["models"]["default"] = registry_default
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(reg_doc, sort_keys=False), encoding="utf-8"
    )
    cfg_doc: dict = {
        "models": {"registry_path": str(tmp_path / "models.yaml"),
                   "rules_path": "rules.yaml"}
    }
    if config_default is not None:
        cfg_doc["models"]["default"] = config_default
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(cfg_doc, sort_keys=False), encoding="utf-8"
    )
    if customs_default is not None:
        (tmp_path / "models.custom.yaml").write_text(
            yaml.safe_dump(
                {"models": {"providers": {}, "default": customs_default}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    return config_path


def test_set_writes_config_not_registry(tmp_path: Path):
    """set_default_model persists to config.yaml; models.yaml is
    byte-identical afterwards (the generator owns that file now)."""
    config_path = _write(tmp_path)
    registry = tmp_path / "models.yaml"
    before = registry.read_bytes()
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model") == "ollama/b-model"
    assert registry.read_bytes() == before
    assert "default" not in yaml.safe_load(before.decode("utf-8"))["models"]

    reloaded = ConfigManager(config_path=config_path)
    reloaded.load()
    assert reloaded.get_default_model() == "ollama/b-model"
    # ... and the value is in the config file, not the registry.
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["models"]["default"] == "ollama/b-model"


def test_set_then_sync_output_survives(tmp_path: Path):
    """The plan gate: set → regenerate (providers-only) → reload keeps
    the selection. Simulated with write_registry, the exact writer
    sync_registry uses."""
    config_path = _write(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    cm.set_default_model("opencode/a-model")

    # A sync rewrites the registry from live sources (new provider
    # appears, legacy default key dropped) — the selection survives.
    write_registry(
        tmp_path / "models.yaml",
        {"opencode": ["a-model"], "zai": ["glm-5"]},
    )
    dumped = yaml.safe_load(
        (tmp_path / "models.yaml").read_text(encoding="utf-8")
    )
    assert "default" not in dumped["models"]

    reloaded = ConfigManager(config_path=config_path)
    reloaded.load()
    assert reloaded.get_default_model() == "opencode/a-model"


def test_parallel_writer_cannot_clobber(tmp_path: Path):
    """A stale/external writer rewriting the registry default (the old
    sync path, a parallel session, a hand edit) no longer moves the
    user's selection: config wins on the read path."""
    config_path = _write(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    cm.set_default_model("ollama/b-model")

    # External writer changes the legacy key out from under us.
    reg = yaml.safe_load((tmp_path / "models.yaml").read_text(encoding="utf-8"))
    reg["models"]["default"] = "opencode/a-model"
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(reg, sort_keys=False), encoding="utf-8"
    )

    reloaded = ConfigManager(config_path=config_path)
    reloaded.load()
    assert reloaded.get_default_model() == "ollama/b-model"


def test_legacy_adopted_iff_config_unset(tmp_path: Path):
    """Load adopts a selectable legacy registry default into an
    existing config.yaml exactly once (idempotent)."""
    config_path = _write(tmp_path, registry_default="ollama/b-model")
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == "ollama/b-model"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["models"]["default"] == "ollama/b-model"
    # The legacy key is left in place (no destructive rewrite)...
    reg = yaml.safe_load((tmp_path / "models.yaml").read_text(encoding="utf-8"))
    assert reg["models"]["default"] == "ollama/b-model"
    # ... but henceforth ignored: point it elsewhere, config wins.
    reg["models"]["default"] = "opencode/a-model"
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(reg, sort_keys=False), encoding="utf-8"
    )
    again = ConfigManager(config_path=config_path)
    again.load()
    assert again.get_default_model() == "ollama/b-model"


def test_config_wins_over_legacy_when_both_set(tmp_path: Path):
    config_path = _write(
        tmp_path,
        registry_default="opencode/a-model",
        config_default="ollama/b-model",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == "ollama/b-model"


def test_unselectable_legacy_is_not_adopted(tmp_path: Path, tmp_home: Path):
    """A legacy default naming a model gone from the registry is left
    alone (no freezing a dead value into config); reads fall through
    to the provider-order fallback. tmp_home keeps the opencode.json
    fallback hermetic (absent → provider order)."""
    config_path = _write(tmp_path, registry_default="opencode/vanished-model")
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == "opencode/a-model"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "default" not in raw["models"]


def test_customs_default_is_never_adopted(tmp_path: Path):
    """The live customs layer stays dynamic: a customs default is NOT
    frozen into config on load (it keeps winning until the user sets
    an explicit config value)."""
    config_path = _write(tmp_path, customs_default="ollama/b-model")
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == "ollama/b-model"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "default" not in raw["models"]


def test_surgical_writer_preserves_comments(tmp_path: Path):
    """The config edit preserves comments and unrelated keys
    byte-for-byte (a YAML round-trip would strip them)."""
    config_path = tmp_path / "config.yaml"
    original = (
        "# header comment\n"
        "server:\n"
        "  host: \"127.0.0.1\"  # keep me\n"
        "\n"
        "models:\n"
        "  registry_path: \"models.yaml\"  # trailing\n"
        "  hot_reload: true\n"
    )
    config_path.write_text(original, encoding="utf-8")
    cm = ConfigManager(config_path=config_path)
    cm._persist_config_default("ollama/b-model")
    text = config_path.read_text(encoding="utf-8")
    assert text == original.replace(
        "models:\n", "models:\n  default: ollama/b-model\n"
    )

    # Second write replaces the line in place (same indent style).
    cm._persist_config_default("opencode/a-model")
    text2 = config_path.read_text(encoding="utf-8")
    assert "default: opencode/a-model" in text2
    assert text2.count("default:") == 1
    assert "# keep me" in text2 and "# trailing" in text2


def test_set_models_default_line_unit():
    """Pure-helper contract: replace in place, insert after the
    anchor, refuse unanchored shapes (caller falls back to a YAML
    rewrite)."""
    assert _set_models_default_line("models:\n  a: 1\n", "x/y") == (
        "models:\n  default: x/y\n  a: 1\n"
    )
    assert _set_models_default_line(
        "models:\n  default: old\n  a: 1\n", "x/y"
    ) == "models:\n  default: x/y\n  a: 1\n"
    assert _set_models_default_line("models: {a: 1}\n", "x/y") is None
    assert _set_models_default_line("server:\n  a: 1\n", "x/y") is None


def test_regenerate_endpoint_calls_sync_registry(tmp_path: Path, monkeypatch):
    """POST /api/models/regenerate funnels into sync_registry (the old
    shell-out to generate_models.py never even wrote the file)."""
    calls: dict = {}

    def fake_sync(path, **kwargs):
        calls["path"] = path
        return {
            "providers": 2, "models": 5, "added": 1, "removed": 0,
            "source": "fake", "path": str(path),
        }

    monkeypatch.setattr(models_sync_mod, "sync_registry", fake_sync)

    from sweave.web.state import AppState

    state = AppState.__new__(AppState)
    from sweave.config.manager import ConfigManager as CM

    registry = tmp_path / "models.yaml"
    registry.write_text(
        yaml.safe_dump(
            {"models": {"providers": {"opencode": ["a-model"]}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"models": {"registry_path": str(registry)}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    state.config_manager = CM(config_path=config_path)
    state.config_manager.load()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(config_router.router)
    app.dependency_overrides[config_router.get_state] = lambda: state
    client = TestClient(app)
    r = client.post("/api/models/regenerate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["source"] == "fake"
    assert calls["path"] == registry


def test_regenerate_endpoint_maps_sync_failure(tmp_path: Path, monkeypatch):
    def boom(path, **kwargs):
        raise RuntimeError("no binary")

    monkeypatch.setattr(models_sync_mod, "sync_registry", boom)

    from fastapi import FastAPI

    from sweave.web.state import AppState

    state = AppState.__new__(AppState)
    from sweave.config.manager import ConfigManager as CM

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    state.config_manager = CM(config_path=config_path)

    app = FastAPI()
    app.include_router(config_router.router)
    app.dependency_overrides[config_router.get_state] = lambda: state
    client = TestClient(app)
    r = client.post("/api/models/regenerate")
    assert r.status_code == 500


def test_validation_unchanged_on_new_home(tmp_path: Path):
    """Allowlist behavior is identical — only the write home moved."""
    config_path = _write(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    with pytest.raises(ValueError, match="qualified"):
        cm.set_default_model("b-model")
    with pytest.raises(ValueError, match="unknown model"):
        cm.set_default_model("nope/nothing-xyz")

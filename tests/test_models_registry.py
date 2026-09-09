"""Single global model registry tests.

The registry (models.yaml) is one provider-grouped list every
specialist picks from — no per-role lists. These tests pin:

* ``get_all_models`` returns QUALIFIED ``provider/model`` ids (the
  opencode wire form; bare ids like ``@cf/...`` 500 the serve because
  they parse to a bogus provider).
* ``get_default_model`` is qualified, in the registry, and prefers
  the user's opencode.json model (the serve's own working default).
* ``set_default_model`` rejects unqualified/unknown models (400 path)
  and persists valid ones (UTF-8, re-loadable).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sweave.config.manager import ConfigManager


def _manager() -> ConfigManager:
    cm = ConfigManager()
    cm.load()
    return cm


def test_all_models_are_qualified():
    cm = _manager()
    all_models = cm.get_all_models()
    assert all_models, "registry is empty"
    assert all("/" in m for m in all_models)


def test_default_is_qualified_and_in_registry():
    cm = _manager()
    default = cm.get_default_model()
    assert "/" in default
    assert default in set(cm.get_all_models())


def test_default_prefers_opencode_configured_model(tmp_path: Path, tmp_home: Path):
    """The user's opencode.json model is the fallback default.

    Hermetic: a tmp registry WITHOUT an explicit ``default`` must
    resolve to the serve's own configured model. The fake
    opencode.json is planted in the test home (the suite isolates
    home per test, so the real user file is never read — 2026-09-09:
    asserting against the real models.yaml coupled the test to
    whatever default was last generated on this machine, and the
    suite-wide home isolation would otherwise skip it always).
    """
    import json

    from sweave.config.manager import ConfigManager as CM

    fake_configured = "opencode/test-fallback-model"
    opencode_json = tmp_home / ".config" / "opencode" / "opencode.json"
    opencode_json.parent.mkdir(parents=True, exist_ok=True)
    opencode_json.write_text(json.dumps({"model": fake_configured}), encoding="utf-8")
    assert CM._opencode_configured_model() == fake_configured
    registry = tmp_path / "models.yaml"
    registry.write_text(
        yaml.safe_dump(
            {"models": {"providers": {"opencode": ["a-model"], "ollama": ["b-model"]}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"models": {"registry_path": str(registry), "rules_path": "rules.yaml"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == fake_configured


def test_set_default_model_rejects_unqualified():
    cm = _manager()
    with pytest.raises(ValueError, match="qualified"):
        cm.set_default_model("gmi")


def test_set_default_model_rejects_unknown():
    cm = _manager()
    with pytest.raises(ValueError, match="unknown model"):
        cm.set_default_model("nope/nothing-here-xyz")


def test_set_default_model_persists_and_reloads(tmp_path: Path):
    """Round-trip through a tmp registry (never touches the real file)."""
    registry = tmp_path / "models.yaml"
    registry.write_text(
        yaml.safe_dump(
            {"models": {"providers": {"opencode": ["a-model"], "ollama": ["b-model"]}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"models": {"registry_path": str(registry), "rules_path": "rules.yaml"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model") == "ollama/b-model"

    reloaded = ConfigManager(config_path=config_path)
    reloaded.load()
    assert reloaded.get_default_model() == "ollama/b-model"

    with pytest.raises(ValueError):
        cm.set_default_model("opencode/a-model-missing")

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


def _manager(tmp_path: Path) -> ConfigManager:
    """ConfigManager over tmp COPIES of the repo files (or a synthetic
    seed on fresh clones without the generated registry).

    Hygiene (user ruling: config.yaml is a working artifact, generated
    files are untracked): no test may load — let alone write — the
    live repo files. ``load()`` persists legacy adoption and
    ``set_default`` writes, so even read-looking tests copy first.
    The copies keep the live-data assertions (real registry shape);
    writes land in tmp and die with it.
    """
    from tests.conftest import repo_config_pair

    cm = ConfigManager(config_path=repo_config_pair(tmp_path))
    cm.load()
    return cm


def test_all_models_are_qualified(tmp_path: Path):
    cm = _manager(tmp_path)
    all_models = cm.get_all_models()
    assert all_models, "registry is empty"
    assert all("/" in m for m in all_models)


def test_qualify_always_prefixes():
    """Bare ids that start with their provider name (nvidia/...,
    openrouter/auto) still get the provider prefix: the prefix is
    part of the MODEL id and the serve's model key is the full
    ``provider/model`` pair. The old skip-if-prefixed rule produced
    single-prefixed ids whose wire modelID missed the serve key."""
    from sweave.config.manager import ConfigManager as CM

    assert CM._qualify("nvidia", "nvidia/active-speaker-detection") == (
        "nvidia/nvidia/active-speaker-detection"
    )
    assert CM._qualify("openrouter", "openrouter/auto") == (
        "openrouter/openrouter/auto"
    )
    assert CM._qualify("ollama", "qwen3:8b") == "ollama/qwen3:8b"

    from sweave.runtime.specialist_store import parse_model_ref
    from sweave.harness.base import model_ref_to_wire

    # Round-trip: the wire modelID equals the serve's model key.
    ref = parse_model_ref("nvidia/nvidia/active-speaker-detection")
    assert ref == {"provider": "nvidia", "model_id": "nvidia/active-speaker-detection"}
    assert model_ref_to_wire(ref) == {
        "providerID": "nvidia",
        "modelID": "nvidia/active-speaker-detection",
    }


def test_default_is_qualified_and_in_registry(tmp_path: Path):
    cm = _manager(tmp_path)
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


def test_set_default_model_rejects_unqualified(tmp_path: Path):
    cm = _manager(tmp_path)
    with pytest.raises(ValueError, match="qualified"):
        cm.set_default_model("gmi")


def test_set_default_model_rejects_unknown(tmp_path: Path):
    cm = _manager(tmp_path)
    with pytest.raises(ValueError, match="unknown model"):
        cm.set_default_model("nope/nothing-here-xyz")


def test_repo_pair_synthetic_seed_without_generated_registry(tmp_path: Path):
    """Fresh-clone resilience: with no repo models.yaml (generated,
    untracked), the helper plants a minimal synthetic registry so
    live-data assertions still hold and nothing touches live files."""
    from tests.conftest import repo_config_pair

    empty = tmp_path / "empty-root"
    empty.mkdir()
    config_path = repo_config_pair(tmp_path, src=empty)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert set(cm.get_all_models()) == {"opencode/a-model", "ollama/b-model"}
    assert cm.get_default_model() == "opencode/a-model"


def test_load_with_default_present_writes_nothing(tmp_path: Path):
    """Pin: ``load()`` is read-only when the config already has a
    default — the adoption write fires only for the missing-default
    case. (A suite run once rewrote the repo's live config.yaml via
    a bare ``ConfigManager()`` at repo CWD; hermetic managers plus
    this pin close that class.)"""
    cm_path = _manager(tmp_path).config_path
    before_cfg = (tmp_path / "config.yaml").read_bytes()
    before_reg = (tmp_path / "models.yaml").read_bytes()
    ConfigManager(config_path=cm_path).load()
    assert (tmp_path / "config.yaml").read_bytes() == before_cfg
    assert (tmp_path / "models.yaml").read_bytes() == before_reg


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


def _tmp_config(tmp_path: Path) -> Path:
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
    return config_path


def test_customs_file_merges_additively(tmp_path: Path):
    """models.custom.yaml adds providers/rows/variants; base rows intact."""
    config_path = _tmp_config(tmp_path)
    (tmp_path / "models.custom.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "providers": {
                        "opencode": ["a-model+high"],
                        "mine": ["m+max"],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    all_models = cm.get_all_models()
    assert "opencode/a-model" in all_models
    assert "opencode/a-model+high" in all_models
    assert "mine/m+max" in all_models


def test_customs_missing_is_noop(tmp_path: Path):
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_all_models() == ["opencode/a-model", "ollama/b-model"]


def test_customs_default_override(tmp_path: Path):
    config_path = _tmp_config(tmp_path)
    (tmp_path / "models.custom.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "providers": {"ollama": ["b-model+low"]},
                    "default": "ollama/b-model+low",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_default_model() == "ollama/b-model+low"


def test_set_default_model_accepts_variant_row(tmp_path: Path):
    config_path = _tmp_config(tmp_path)
    (tmp_path / "models.custom.yaml").write_text(
        yaml.safe_dump(
            {"models": {"providers": {"ollama": ["b-model+low"]}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model+low") == "ollama/b-model+low"


def test_get_model_meta(tmp_path: Path):
    import json

    config_path = _tmp_config(tmp_path)
    (tmp_path / "models.meta.json").write_text(
        json.dumps(
            {
                "ollama/b-model": {
                    "provider": "ollama",
                    "model": "b-model",
                    "variants": ["low"],
                    "limit": {"context": 8, "output": 2},
                }
            }
        ),
        encoding="utf-8",
    )
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_model_meta("ollama/b-model")["variants"] == ["low"]
    assert cm.get_model_meta("ollama/b-model")["limit"] == {"context": 8, "output": 2}
    assert cm.get_model_meta("nope/nothing") == {}


def test_get_model_meta_without_sidecar(tmp_path: Path):
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.get_model_meta("ollama/b-model") == {}
    assert cm.get_model_meta() == {}


def _tmp_config_with_meta(tmp_path: Path) -> Path:
    import json

    config_path = _tmp_config(tmp_path)
    (tmp_path / "models.meta.json").write_text(
        json.dumps({"ollama/b-model": {"variants": ["low", "high"]}}),
        encoding="utf-8",
    )
    return config_path


def test_set_default_model_accepts_advertised_variant(tmp_path: Path):
    """A variant on the offered default is VALIDATED against the
    sidecar, but the STORED default stays bare (M1.13 step 3 ruling
    2026-09-10: no ``+variant`` suffix in models.yaml — the env-form
    OPENCODE_MODEL must be bare, harness/opencode.py:980-988)."""
    config_path = _tmp_config_with_meta(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model+low") == "ollama/b-model"
    assert cm.get_default_model() == "ollama/b-model"


def test_set_default_model_stores_bare_default(tmp_path: Path):
    """Round-trip: a variant-carrying selection persists BARE."""
    config_path = _tmp_config_with_meta(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    cm.set_default_model("ollama/b-model+low")
    reloaded = ConfigManager(config_path=config_path)
    reloaded.load()
    assert reloaded.get_default_model() == "ollama/b-model"


def test_set_default_model_fires_reload_callbacks(tmp_path: Path):
    """models.yaml writes do NOT fire the ConfigReloader (it watches
    config.yaml only — manager.py:12-30); set_default_model reloads
    programmatically and fans out to the registered callbacks so the
    running server's view stays current (2026-09-10 live probe: the
    in-memory default went stale until a config.yaml mtime touch)."""
    fired: list = []
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    cm.register_reload_callback(lambda old, new: fired.append((old, new)))
    cm.set_default_model("ollama/b-model")
    assert fired, "callback must fire programmatically"
    old, new = fired[0]
    assert old is not None and new is not None
    assert new.models.default == "ollama/b-model"


def test_set_default_model_without_registered_callback_is_ok(tmp_path: Path):
    """No callbacks registered: persisting still works (no reload work)."""
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model") == "ollama/b-model"


def test_set_default_model_rejects_unadvertised_variant(tmp_path: Path):
    config_path = _tmp_config_with_meta(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    with pytest.raises(ValueError, match="unknown variant"):
        cm.set_default_model("ollama/b-model+max")


def test_set_default_model_variant_without_meta(tmp_path: Path):
    """No sidecar: any suffix on a registered base is accepted (the
    serve is the final arbiter; the UI only offers advertised ones).
    Persistence still lands BARE (M1.13 step 3 ruling 2026-09-10)."""
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    assert cm.set_default_model("ollama/b-model+low") == "ollama/b-model"
    assert cm.get_default_model() == "ollama/b-model"


def test_set_default_model_rejects_unknown_base_with_variant(tmp_path: Path):
    config_path = _tmp_config(tmp_path)
    cm = ConfigManager(config_path=config_path)
    cm.load()
    with pytest.raises(ValueError, match="unknown model"):
        cm.set_default_model("nope/nothing+low")

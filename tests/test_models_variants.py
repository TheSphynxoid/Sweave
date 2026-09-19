"""Effort-variant map derivation (two-bucket taxonomy support).

``effort_variants_for`` merges the meta sidecar's per-model lists
with ``+suffix`` registry rows (generated + customs): known models
report advertised values, unknown models report None (unverifiable,
never "invalid"). Pure file readers — hermetic tmp fixtures here.
"""

from __future__ import annotations

import json
from pathlib import Path

from sweave.models_variants import effort_variants_for


def _write(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload) if path.suffix == ".json" else str(payload),
        encoding="utf-8",
    )


def _registry_yaml(providers: dict) -> str:
    lines = ["models:", "  providers:"]
    for provider, rows in providers.items():
        lines.append(f"    {provider}:")
        for row in rows:
            lines.append(f"    - {row}")
    return "\n".join(lines) + "\n"


def test_meta_authoritative_lists(tmp_path: Path):
    meta = {
        "opencode-go/m": {"variants": ["low", "high"]},
    }
    _write(tmp_path / "models.meta.json", meta)
    _write(
        tmp_path / "models.yaml",
        _registry_yaml({"opencode-go": ["m"]}),
    )
    assert effort_variants_for("opencode-go", "m", repo_root=tmp_path) == [
        "high",
        "low",
    ]


def test_registry_rows_supplement_meta(tmp_path: Path):
    meta = {
        "opencode-go/m": {"variants": ["low"]},
    }
    _write(tmp_path / "models.meta.json", meta)
    _write(
        tmp_path / "models.yaml",
        _registry_yaml({"opencode-go": ["m", "m+high", "m+max"]}),
    )
    assert effort_variants_for("opencode-go", "m", repo_root=tmp_path) == [
        "high",
        "low",
        "max",
    ]


def test_customs_rows_count(tmp_path: Path):
    _write(tmp_path / "models.meta.json", {})
    _write(tmp_path / "models.yaml", _registry_yaml({"mine": ["m"]}))
    customs = tmp_path / "models.custom.yaml"
    customs.write_text(
        _registry_yaml({"mine": ["m+thinking"]}), encoding="utf-8"
    )
    assert effort_variants_for("mine", "m", repo_root=tmp_path) == ["thinking"]


def test_known_model_no_values_reports_empty(tmp_path: Path):
    _write(tmp_path / "models.meta.json", {})
    _write(tmp_path / "models.yaml", _registry_yaml({"p": ["m"]}))
    assert effort_variants_for("p", "m", repo_root=tmp_path) == []


def test_unknown_model_reports_none(tmp_path: Path):
    _write(tmp_path / "models.meta.json", {})
    _write(tmp_path / "models.yaml", _registry_yaml({"p": ["other"]}))
    assert effort_variants_for("p", "m", repo_root=tmp_path) is None
    assert effort_variants_for("nope", "m", repo_root=tmp_path) is None
    assert effort_variants_for(None, None, repo_root=tmp_path) is None


def test_missing_files_degrade_to_none(tmp_path: Path):
    assert effort_variants_for("p", "m", repo_root=tmp_path) is None


def test_bad_rows_never_raise(tmp_path: Path):
    _write(tmp_path / "models.meta.json", {"p/m": {"variants": "nope"}})
    reg = tmp_path / "models.yaml"
    reg.write_text(
        "models:\n  providers:\n    p:\n    - m\n    - 42\n",
        encoding="utf-8",
    )
    assert effort_variants_for("p", "m", repo_root=tmp_path) == []

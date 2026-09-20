"""Serve overlay is optional (2026-09-20: opencode binary absent on
engine-default boxes — the sync button 500'd before any work).

models.dev is the canonical source (the overlay only appends
local-only rows), so a missing binary skips the overlay with an
honest report instead of failing the sync. Both layers missing
still fails loud (empty registry is never written).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import sweave.models_sync as models_sync

UPSTREAM_RAW = {
    "testprov": {
        "models": {
            "m1": {"id": "m1"},
            "m2": {"id": "m2", "reasoning_options": [{"type": "effort", "values": ["low"]}]},
        }
    }
}


def _seed_registry(path: Path, providers: dict) -> None:
    path.write_text(
        yaml.safe_dump({"models": {"providers": providers}}, sort_keys=False),
        encoding="utf-8",
    )


def test_sync_without_binary_uses_upstream_only(tmp_path: Path, monkeypatch):
    """No opencode binary: models.dev rows land, overlay is empty,
    the report names the shape, the file is written."""
    monkeypatch.setattr(models_sync, "resolve_opencode_binary", lambda: None)
    monkeypatch.setattr(models_sync, "fetch_models_dev", lambda: UPSTREAM_RAW)

    registry = tmp_path / "models.yaml"
    _seed_registry(registry, {"testprov": []})

    report = models_sync.sync_registry(registry)

    assert "no serve overlay" in report["source"]
    assert report["overlay_rows"] == 0
    assert report["overlay"] == {}
    assert report["overlay_skipped"] is True
    assert report["models"] >= 2
    written = yaml.safe_load(registry.read_text(encoding="utf-8"))
    rows = written["models"]["providers"]["testprov"]
    assert "m1" in rows and "m2" in rows


def test_sync_without_binary_and_offline_fails_loud(tmp_path: Path, monkeypatch):
    """Neither layer: RuntimeError, registry untouched (never an
    empty write)."""
    monkeypatch.setattr(models_sync, "resolve_opencode_binary", lambda: None)

    def _offline():
        raise ConnectionError("no route")

    monkeypatch.setattr(models_sync, "fetch_models_dev", _offline)

    registry = tmp_path / "models.yaml"
    with pytest.raises(RuntimeError, match="empty registry"):
        models_sync.sync_registry(registry)
    assert not registry.exists()

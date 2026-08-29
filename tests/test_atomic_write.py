"""Atomic JSON write contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweave.runtime.locking import atomic_write_json, atomic_write_json_sync


def test_sync_write_creates_file(tmp_path: Path):
    target = tmp_path / "data.json"
    atomic_write_json_sync(target, {"hello": "world", "n": 42})
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"hello": "world", "n": 42}


def test_sync_write_overwrites_existing(tmp_path: Path):
    target = tmp_path / "data.json"
    target.write_text('{"old": true}', encoding="utf-8")
    atomic_write_json_sync(target, {"new": True})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"new": True}


def test_sync_write_creates_parent_dir(tmp_path: Path):
    target = tmp_path / "nested" / "deeper" / "data.json"
    atomic_write_json_sync(target, {"x": 1})
    assert target.exists()


def test_sync_write_uses_yaml_when_requested(tmp_path: Path):
    target = tmp_path / "data.yaml"
    atomic_write_json_sync(target, {"hello": "world"}, use_yaml=True)
    text = target.read_text(encoding="utf-8")
    assert "hello" in text
    assert "world" in text


def test_sync_write_cleans_up_tmp_on_failure(tmp_path: Path):
    target = tmp_path / "data.json"
    # Pass a non-serialisable object to force json.dump to raise.
    with pytest.raises(TypeError):
        atomic_write_json_sync(target, {"bad": set()})  # set is not JSON-serialisable
    # No tmp leftovers
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"leftover tmp files: {leftovers}"
    # Target was not created
    assert not target.exists()


@pytest.mark.asyncio
async def test_async_wrapper_works(tmp_path: Path):
    target = tmp_path / "data.json"
    await atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}

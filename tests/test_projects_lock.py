"""Per-project write-lock tests for ProjectManager."""

from __future__ import annotations

import threading
from pathlib import Path

from sweave.projects import ProjectManager


def test_lock_for_returns_same_lock(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    project_path = tmp_path / "p-lock"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-lock", project_path)
    a = pm._lock_for("p-lock")
    b = pm._lock_for("p-lock")
    assert a is b


def test_different_projects_dont_share_lock(tmp_project_manager: ProjectManager, tmp_path: Path):
    pm = tmp_project_manager
    for name in ("a", "b"):
        pp = tmp_path / name
        pp.mkdir(parents=True, exist_ok=True)
        pm.create_project(name, pp)
    assert pm._lock_for("a") is not pm._lock_for("b")


def test_concurrent_writes_to_same_project_dont_corrupt(
    tmp_project_manager: ProjectManager, tmp_path: Path
):
    """Many threads append to the same session. Final state must be valid."""
    pm = tmp_project_manager
    project_path = tmp_path / "p-conc"
    project_path.mkdir(parents=True, exist_ok=True)
    pm.create_project("p-conc", project_path)
    s = pm.create_session("p-conc", "S1")

    def writer(n: int) -> None:
        for i in range(50):
            pm.append_message(s.id, "user", f"thread-{n}-msg-{i}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sfile = pm.projects_dir / "p-conc" / "sessions" / f"{s.id}.json"
    data = __import__("json").loads(sfile.read_text(encoding="utf-8"))
    assert data["id"] == s.id
    # Every append wrote exactly one line: 4 threads x 50 appends.
    mfile = pm.projects_dir / "p-conc" / "sessions" / f"{s.id}.messages.jsonl"
    lines = [ln for ln in mfile.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 200

"""Filesystem browser routes (drives, list, validate, create directory)."""

from __future__ import annotations

import platform
import string
from pathlib import Path as PathLib

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


class DirectoryBrowser:
    """Cross-platform directory browser for the project picker."""

    @staticmethod
    def get_drives() -> list[dict]:
        system = platform.system()
        if system == "Windows":
            drives: list[dict] = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if PathLib(drive).exists():
                    drives.append({"name": f"{letter}:", "path": drive, "is_dir": True})
            return drives
        return [{"name": "/", "path": "/", "is_dir": True}]

    @staticmethod
    def list_directory(path: str) -> dict:
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"error": f"Path does not exist: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}
            parent = str(p.parent) if p.parent != p else None
            entries: list[dict] = []
            if parent and parent != str(p):
                entries.append({"name": "..", "path": parent, "is_dir": True})
            try:
                for entry in sorted(
                    p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
                ):
                    try:
                        if not entry.is_dir():
                            continue
                        entries.append(
                            {"name": entry.name, "path": str(entry), "is_dir": True}
                        )
                    except (PermissionError, OSError):
                        continue
            except PermissionError:
                return {"error": f"Permission denied: {path}"}
            return {
                "path": str(p),
                "parent": parent,
                "entries": entries[:200],
                "total": len(entries),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def validate_path(path: str) -> dict:
        try:
            p = PathLib(path).expanduser().resolve()
            if not p.exists():
                return {"valid": False, "error": "Path does not exist"}
            if not p.is_dir():
                return {"valid": False, "error": "Path is not a directory"}
            return {"valid": True, "path": str(p), "name": p.name}
        except Exception as e:
            return {"valid": False, "error": str(e)}


@router.get("/api/fs/drives")
async def api_get_drives():
    return {"drives": DirectoryBrowser.get_drives()}


@router.get("/api/fs/list")
async def api_list_directory(path: str):
    result = DirectoryBrowser.list_directory(path)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/api/fs/validate")
async def api_validate_path(request: Request):
    body = await request.json()
    return DirectoryBrowser.validate_path(body.get("path", ""))


@router.post("/api/fs/create")
async def api_create_directory(request: Request):
    body = await request.json()
    path = body.get("path", "")
    name = body.get("name", "")
    if not path or not name:
        raise HTTPException(400, "Path and name required")
    try:
        safe_name = "".join(
            c for c in name if c.isalnum() or c in ("-", "_", " ")
        ).strip()
        if not safe_name:
            raise HTTPException(400, "Invalid name")
        p = PathLib(path).expanduser()
        if p.is_file():
            p = p.parent
        elif not p.is_dir():
            p = PathLib(path).parent
        new_path = (p / safe_name).resolve()
        if new_path.exists():
            raise HTTPException(400, f"Path already exists: {new_path}")
        new_path.mkdir(parents=True, exist_ok=False)
        return {"success": True, "path": str(new_path), "name": safe_name}
    except FileExistsError:
        raise HTTPException(400, "Path already exists")
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

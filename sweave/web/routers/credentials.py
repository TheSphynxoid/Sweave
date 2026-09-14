"""Provider credential + availability routes (credentials owned by Sweave).

User ruling 2026-09-14: ``~/.sweave/credentials.json`` is canonical;
the opencode auth store is an import source (adopt once, drift prompts,
reverse-sync ours→theirs). Secrets never leave in GET responses —
only fingerprints, suffixes, and sources. No WS events (nothing
about credentials is broadcast; callers refetch on mutation).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sweave.credentials import (
    LOCAL_NO_KEY_PROVIDERS,
    CredentialStore,
    credential_source,
    opencode_auth_paths,
    read_opencode_store,
    resolve_provider,
    sync_with_opencode,
    write_opencode_store,
)
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


def _store() -> CredentialStore:
    return CredentialStore()


class CredentialSetRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    key: str = Field(..., min_length=1, max_length=4096)


class CredentialImportRequest(BaseModel):
    providers: Optional[list[str]] = None


class CredentialResolveRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    choice: str = Field(..., description="'mine' (push ours everywhere) or 'theirs' (adopt + converge)")
    source: Optional[str] = Field(
        default=None,
        description="Optional opencode-store path to adopt from (choice='theirs')",
    )


@router.get("/api/providers")
async def list_providers(state: AppState = Depends(get_state)):
    """The absolute catalog (universe) annotated with availability.

    ``connected`` = a credential exists in some tier (env → sweave →
    opencode-legacy); ``via`` names the tier. Models come from the
    synced catalog (models.dev universe) — never filtered by what
    opencode supports.
    """
    store = _store()
    catalog = state.config_manager.get_models()
    providers = catalog.providers or {}
    out = []
    for pid in sorted(providers):
        models = providers[pid]
        if pid in LOCAL_NO_KEY_PROVIDERS:
            # Local serves: marked, never managed (special care
            # later — reachability, not keys).
            out.append(
                {
                    "id": pid,
                    "models": list(models) if isinstance(models, list) else [],
                    "connected": None,
                    "via": None,
                    "key_suffix": None,
                    "local": True,
                }
            )
            continue
        entry = store.load()["providers"].get(pid)
        suffix = (
            entry.get("key_suffix")
            if isinstance(entry, dict) and entry.get("type") == "api_key"
            else None
        )
        out.append(
            {
                "id": pid,
                "models": list(models) if isinstance(models, list) else [],
                "connected": credential_source(pid, store) is not None,
                "via": credential_source(pid, store),
                "key_suffix": suffix,
                "local": False,
            }
        )
    pending = sync_pending_snapshot(store)
    return {"providers": out, "pending_imports": pending}


def sync_pending_snapshot(store: CredentialStore) -> list[dict[str, Any]]:
    # Per-provider merge across BOTH opencode stores (isolated copy +
    # real). The stores can genuinely disagree (fp-divergence seen live
    # 2026-09-14: isolated vs real held different opencode keys), and a
    # plain provider-name list ping-pongs forever — adopting store A's
    # key makes store B "rotated" and vice versa. So each item carries
    # the disagreeing `sources` + the first-seen `reason`; resolution
    # (POST /api/credentials/resolve) converges all stores at once.
    merged: dict[str, dict[str, Any]] = {}
    for path in opencode_auth_paths():
        if not path.is_file():
            continue
        for item in store.pending_imports(read_opencode_store(path)):
            name = item["provider"]
            entry = merged.setdefault(
                name, {"provider": name, "reason": item.get("reason", ""), "sources": []}
            )
            if str(path) not in entry["sources"]:
                entry["sources"].append(str(path))
    return sorted(merged.values(), key=lambda r: r["provider"])


@router.get("/api/credentials/pending")
async def pending_imports(state: AppState = Depends(get_state)):
    del state  # home-anchored store; no app state needed
    return {"pending": sync_pending_snapshot(_store())}


@router.post("/api/credentials/import")
async def import_credentials(
    request: CredentialImportRequest, state: AppState = Depends(get_state)
):
    del state
    store = _store()
    wanted = (
        {p.strip() for p in request.providers if p and p.strip()}
        if request.providers
        else None
    )
    adopted: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in opencode_auth_paths():
        if not path.is_file():
            continue
        current = read_opencode_store(path)
        subset = (
            {k: v for k, v in current.items() if k in wanted}
            if wanted is not None
            else current
        )
        result = store.adopt(subset, force=True)
        adopted.extend(n for n in result["adopted"] if n not in adopted)
        skipped.extend(result["skipped"])
    return {"adopted": sorted(adopted), "skipped": skipped}


@router.post("/api/credentials", status_code=201)
async def set_credential(
    request: CredentialSetRequest, state: AppState = Depends(get_state)
):
    del state
    provider = request.provider.strip()
    if not provider:
        raise HTTPException(400, "provider must be a non-empty string")
    store = _store()
    try:
        entry = store.set_api_key(provider, request.key, source="manual")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # Push-through: ours→theirs immediately (the ruling), backup kept.
    pushed: list[str] = []
    for path in opencode_auth_paths():
        if not path.is_file():
            continue
        current = read_opencode_store(path)
        if provider not in store.push_candidates(current):
            continue
        rendered = store.render_push_entry(provider)
        if rendered is None:
            continue
        merged = dict(current)
        prior = merged.get(provider)
        merged[provider] = (
            {**prior, **rendered} if isinstance(prior, dict) else dict(rendered)
        )
        try:
            write_opencode_store(path, merged, backup=True)
            pushed.append(str(path))
        except Exception as e:  # noqa: BLE001 — set stands, push retries
            import logging

            logging.getLogger(__name__).warning(
                "credentials: push-through failed for %s: %s", path, e
            )
    return {"credential": entry, "pushed": pushed}


@router.delete("/api/credentials/{provider}")
async def delete_credential(provider: str, state: AppState = Depends(get_state)):
    del state
    if not _store().delete(provider):
        raise HTTPException(404, f"no Sweave credential for {provider!r}")
    return {"success": True, "provider": provider}


@router.post("/api/credentials/sync")
async def sync_credentials(state: AppState = Depends(get_state)):
    """Run the boot sync on demand (adopt-in + push-out)."""
    del state
    return sync_with_opencode()


@router.post("/api/credentials/resolve", status_code=200)
async def resolve_credential(
    request: CredentialResolveRequest, state: AppState = Depends(get_state)
):
    """Converge one diverged provider (the Adopt-All ping-pong fix).

    When the isolated + real opencode stores disagree, bulk import
    flips which side reads "rotated" forever. This picks a winner and
    writes it to every store at once (backups kept): ``mine`` pushes
    our key out; ``theirs`` adopts the disagreeing store's key
    (optionally from ``source``) and converges the rest onto it.
    """
    del state
    try:
        return resolve_provider(
            request.provider, request.choice, source=request.source
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

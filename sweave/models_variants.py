"""Advertised reasoning-effort values per model (two-bucket taxonomy).

The registry knows which ``+variant`` values each model offers, from
two sources: the generated ``models.yaml`` rows (``model+variant``
suffixes, including ``models.custom.yaml`` additions) and the
``models.meta.json`` sidecar (authoritative per-model ``variants``
lists from models.dev ``reasoning_options`` + the serve overlay).

Pure readers over repo files — no manager, no side effects — so
request-time failure classification never depends on server state:

* ``effort_variants_for()`` returns the sorted advertised values, or
  ``None`` when the model is unknown to both sources (customs,
  brand-new, or typo — unverifiable, never "invalid").
* Callers (variant_refused bucketing) treat *known value refused* as
  provider-side drift and *unknown value refused* as a knowledge gap.
  Both are the out-of-sync bucket; only the remedy differs. Unknown
  is never a refusal reason by itself — that would reintroduce
  refusing valid work (custom modes like ``+thinking`` live outside
  every upstream map by design).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        import json

        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def effort_variants_for(
    provider: str | None,
    model_id: str | None,
    *,
    repo_root: Path | None = None,
    registry_path: Path | None = None,
    meta_path: Path | None = None,
) -> list[str] | None:
    """Advertised effort values for ``provider/model_id``.

    Merges the meta sidecar's ``variants`` list with ``+suffix`` rows
    from the registry (generated + customs). Returns ``None`` when the
    model is unknown to both sources. An empty list means a known
    model with no advertised effort values. Never raises.
    """
    if not provider or not model_id:
        return None
    root = Path(repo_root) if repo_root is not None else _repo_root()
    reg_path = (
        Path(registry_path)
        if registry_path is not None
        else root / "models.yaml"
    )
    meta_p = (
        Path(meta_path) if meta_path is not None else root / "models.meta.json"
    )
    found_model = False
    variants: set[str] = set()

    meta = _read_json_mapping(meta_p)
    entry = meta.get(f"{provider}/{model_id}")
    if isinstance(entry, dict):
        found_model = True
        declared = entry.get("variants")
        if isinstance(declared, list):
            for value in declared:
                if isinstance(value, str) and value:
                    variants.add(value)

    registry = _read_yaml_mapping(reg_path)
    providers = registry.get("models", {}).get("providers", {})
    if not isinstance(providers, dict):
        providers = registry.get("providers", {})
    # Customs overlay (user-authored rows, same shapes as the
    # manager's merge: `{models: ...}` or raw providers). A custom
    # provider/model is verifiable against its own file.
    customs = _read_yaml_mapping(reg_path.parent / "models.custom.yaml")
    customs_models = customs.get("models", customs)
    if isinstance(customs_models, dict):
        customs_providers = customs_models.get("providers", customs_models)
        if isinstance(customs_providers, dict):
            merged_providers = (
                dict(providers) if isinstance(providers, dict) else {}
            )
            for provider_name, rows in customs_providers.items():
                if isinstance(rows, list):
                    merged_providers.setdefault(provider_name, [])
                    if isinstance(merged_providers[provider_name], list):
                        merged_providers[provider_name] = [
                            *merged_providers[provider_name],
                            *rows,
                        ]
            providers = merged_providers
    rows = providers.get(provider) if isinstance(providers, dict) else None
    if isinstance(rows, list):
        from sweave.runtime.specialist_store import parse_model_ref

        for row in rows:
            if not isinstance(row, str):
                continue
            try:
                ref = parse_model_ref(f"{provider}/{row}")
            except Exception:  # noqa: BLE001 — one bad row never fails lookup
                continue
            if not ref or ref.get("model_id") != model_id:
                continue
            found_model = True
            variant = ref.get("variant")
            if isinstance(variant, str) and variant:
                variants.add(variant)

    if not found_model:
        return None
    return sorted(variants)

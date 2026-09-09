#!/usr/bin/env python3
"""
Generate models.yaml from opencode's available models.

Single global list grouped by provider (no per-role lists — every
specialist picks from this one registry; the orchestrator uses the
``default``).

Usage:
    python scripts/generate_models.py --write   # writes models.yaml (UTF-8, no BOM)
    python scripts/generate_models.py           # prints to stdout

NOTE: never redirect stdout to the file in PowerShell (``>`` writes
UTF-16 LE with BOM, which breaks PyYAML). Always use ``--write``.
"""

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def get_opencode_models() -> dict[str, list[str]]:
    """Run `cmd /c npx opencode models` and return dict of provider -> [model_ids]."""
    result = subprocess.run(
        ["cmd", "/c", "npx", "opencode", "models"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"Error running opencode models: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    models_by_provider: dict[str, list[str]] = defaultdict(list)
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Format: provider/model_id
        if "/" in line:
            provider, model_id = line.split("/", 1)
            models_by_provider[provider].append(model_id)
    return models_by_provider


def _quote_if_needed(s: str) -> str:
    """Quote YAML strings that start with special characters."""
    if s and s[0] in "@&*!>|'\"%{}[]:,?-#":
        return f'"{s}"'
    return s


def _opencode_configured_model() -> str | None:
    """The model from the user's opencode.json, if set.

    Preferred as the registry ``default``: it is the model the user's
    serve is actually configured to run, so chat works out of the box.
    """
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None
    model = config.get("model") if isinstance(config, dict) else None
    return model if isinstance(model, str) and "/" in model else None


def _pick_default(models_by_provider: dict[str, list[str]]) -> str | None:
    """Pick the registry default: opencode.json model when it is in the
    registry, else the first model of the preferred provider order."""
    available = {
        f"{provider}/{model}"
        for provider, provider_models in models_by_provider.items()
        for model in provider_models
    }
    configured = _opencode_configured_model()
    if configured and configured in available:
        return configured
    if configured:
        # The serve knows this model even if the registry snapshot
        # doesn't list it (custom provider) — still the best default.
        return configured
    for preferred in ("opencode", "ollama", "gmi", "openrouter"):
        provider_models = models_by_provider.get(preferred)
        if provider_models:
            return f"{preferred}/{sorted(provider_models)[0]}"
    for provider in sorted(models_by_provider):
        provider_models = models_by_provider.get(provider)
        if provider_models:
            return f"{provider}/{sorted(provider_models)[0]}"
    return None


def main():
    write = "--write" in sys.argv[1:]
    models_by_provider = get_opencode_models()

    output = ["models:"]
    default = _pick_default(models_by_provider)
    if default:
        output.append(f"  default: {default}")
    output.append("  providers:")

    for provider in sorted(models_by_provider.keys()):
        models = sorted(models_by_provider[provider])
        if not models:
            continue
        output.append(f"    {provider}:")
        for model in models:
            output.append(f"      - {_quote_if_needed(model)}")

    text = "\n".join(output) + "\n"
    if write:
        # UTF-8 without BOM (PowerShell ``>`` would write UTF-16).
        Path("models.yaml").write_text(text, encoding="utf-8")
        print(f"wrote models.yaml (default: {default})")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
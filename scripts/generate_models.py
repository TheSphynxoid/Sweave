#!/usr/bin/env python3
"""
Generate models.yaml from opencode's available models.

Usage:
    python scripts/generate_models.py > models.yaml
"""

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


def select_default(provider: str, models: list[str], role: str) -> str:
    """Select a sensible default model for a role from available models."""
    # Prefer free/cheap models for orchestrator, capable for others
    preferences = {
        "orchestrator": [
            "nemotron-3-ultra-free",
            "nemotron-3.5-lightning-free",
            "deepseek-v4-flash",
            "gemini-3.5-flash",
            "glm-5.3-flash",
            "mimo-v2.5-free",
            "ling-3.0-flash-fin-free",
        ],
        "backend": [
            "glm-5.3",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "glm-5.2",
            "gpt-5",
            "claude-sonnet-4",
            "gemini-3.5-flash",
        ],
        "frontend": [
            "hy3",
            "hy4-preview",
            "nemotron-3-ultra-free",
            "deepseek-v4-flash",
            "llama-3.3-70b-instruct",
            "gpt-5",
            "gemini-3.5-flash",
        ],
        "reviewer": [
            "claude-sonnet-4",
            "claude-sonnet-4.5",
            "gpt-5",
            "gpt-5-pro",
            "llama-3.1-nemotron-70b-instruct",
            "glm-5.3",
            "deepseek-v4-pro",
        ],
    }

    prefs = preferences.get(role, [])
    for pref in prefs:
        for model in models:
            if pref in model:
                return f"{provider}/{model}"
    # Fallback: first model
    return f"{provider}/{models[0]}" if models else ""


def get_aliases(all_models: dict[str, list[str]], default: str, role: str) -> list[str]:
    """Get aliases from ALL providers, excluding the default."""
    aliases = []
    for provider, models in all_models.items():
        for model in models:
            full = f"{provider}/{model}"
            if full != default:
                aliases.append(full)
    return aliases


def main():
    models_by_provider = get_opencode_models()

    # Priority providers for each role (in order of preference for DEFAULT)
    role_provider_priority = {
        "orchestrator": ["opencode", "opencode-go", "nvidia", "cloudflare-workers-ai", "zai", "zai-coding-plan"],
        "backend": ["opencode", "opencode-go", "gmicloud", "nvidia", "zai", "zai-coding-plan"],
        "frontend": ["opencode", "opencode-go", "nvidia", "openrouter", "zai"],
        "reviewer": ["opencode", "openrouter", "nvidia", "gmicloud", "zai"],
    }

    output = ["models:"]

    for role in ["orchestrator", "backend", "frontend", "reviewer"]:
        default = None
        used_provider = None

        # Find default from priority providers
        for provider in role_provider_priority[role]:
            if provider in models_by_provider and models_by_provider[provider]:
                default = select_default(provider, models_by_provider[provider], role)
                if default:
                    used_provider = provider
                    break

        if not default:
            # Fallback to any available provider
            for provider, models in models_by_provider.items():
                if models:
                    default = f"{provider}/{models[0]}"
                    used_provider = provider
                    break

        if not default:
            print(f"Warning: no models found for role {role}", file=sys.stderr)
            continue

        # Get aliases from ALL providers
        aliases = get_aliases(models_by_provider, default, role)

        output.append(f"  {role}:")
        output.append(f"    default: {default}")
        if aliases:
            output.append(f"    aliases:")
            for alias in aliases:
                output.append(f"    - {alias}")
        output.append(f"    provider: {used_provider}")
        output.append(f"    description: {role.capitalize()} specialist")

    print("\n".join(output))


if __name__ == "__main__":
    main()
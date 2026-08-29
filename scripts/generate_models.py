#!/usr/bin/env python3
"""Generate models.yaml from models.dev"""

import asyncio
import json
from pathlib import Path
import httpx
import yaml


MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_PATH = Path.home() / ".cache" / "sweave" / "models.json"


async def fetch_models() -> dict:
    """Fetch model data from models.dev."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Try cache first
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            cached = json.load(f)
            if cached:
                return cached
    
    # Fetch from models.dev
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(MODELS_DEV_URL)
        response.raise_for_status()
        data = response.json()
    
    # Cache
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)
    
    return data


def generate_models_yaml(data: dict) -> dict:
    """Generate Sweave models.yaml from models.dev data."""
    # Provider mapping for Sweave roles
    role_mapping = {
        "orchestrator": {
            "preferred": ["deepseek", "gpt-4o-mini", "claude-3.5-haiku", "gemini-flash"],
            "description": "Fast orchestrator model for routing and coordination",
        },
        "backend": {
            "preferred": ["glm", "deepseek-coder", "qwen2.5-coder", "codellama", "gpt-4o", "claude-3.5-sonnet"],
            "description": "Backend specialist - APIs, databases, server logic",
        },
        "frontend": {
            "preferred": ["hy3", "claude-3.5-sonnet", "gpt-4o", "qwen2.5-vl", "gemini-pro"],
            "description": "Frontend specialist - React, Vue, UI components",
        },
        "reviewer": {
            "preferred": ["claude-3.5-sonnet", "gpt-4o", "claude-3-opus", "gemini-pro"],
            "description": "Code review specialist - security, quality, architecture",
        },
    }
    
    # Flatten models by provider
    models_by_provider = {}
    for provider_id, provider_data in data.items():
        if isinstance(provider_data, dict) and "models" in provider_data:
            for model_id, model_data in provider_data["models"].items():
                if isinstance(model_data, dict):
                    models_by_provider[f"{provider_id}/{model_id}"] = {
                        "id": model_id,
                        "provider": provider_id,
                        "name": model_data.get("name", model_id),
                        "capabilities": model_data,
                    }
    
    # Select best models for each role
    result = {"models": {}}
    
    for role, config in role_mapping.items():
        selected = None
        aliases = []
        
        for pref in config["preferred"]:
            # Find matching models
            matches = [
                (mid, mdata) for mid, mdata in models_by_provider.items()
                if pref.lower() in mid.lower() or pref.lower() in mdata["name"].lower()
            ]
            if matches:
                if selected is None:
                    selected = matches[0][0]
                aliases.extend([m[0] for m in matches[1:]])
        
        if selected:
            result["models"][role] = {
                "default": selected,
                "aliases": list(dict.fromkeys(aliases))[:5],  # Dedupe, max 5
                "provider": "opencode",
                "description": config["description"],
            }
    
    return result


async def main():
    print("Fetching models from models.dev...")
    data = await fetch_models()
    
    print("Generating models.yaml...")
    models_yaml = generate_models_yaml(data)
    
    output_path = Path("models.yaml")
    with open(output_path, "w") as f:
        yaml.safe_dump(models_yaml, f, sort_keys=False)
    
    print(f"Generated {output_path}")
    print(yaml.dump(models_yaml, sort_keys=False))


if __name__ == "__main__":
    asyncio.run(main())
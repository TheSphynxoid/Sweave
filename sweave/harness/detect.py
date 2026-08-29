from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class HarnessInfo:
    """Information about a detected harness."""
    name: str
    display_name: str
    command: str
    available: bool
    version: Optional[str] = None
    providers: list[str] = None
    models: list[str] = None
    
    def __post_init__(self):
        if self.providers is None:
            self.providers = []
        if self.models is None:
            self.models = []


async def detect_opencode() -> HarnessInfo:
    """Detect OpenCode installation and available providers/models."""
    info = HarnessInfo(
        name="opencode",
        display_name="OpenCode",
        command="opencode",
        available=False,
    )
    
    # Check if opencode command exists
    opencode_path = shutil.which("opencode")
    if not opencode_path:
        return info
    
    info.available = True
    info.command = opencode_path
    
    # Get version
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [opencode_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info.version = result.stdout.strip()
    except Exception:
        pass
    
    # Get providers and models from OpenCode config
    try:
        models = await get_opencode_models()
        info.models = list(models.keys())  # provider names
        info.providers = list(models.keys())
    except Exception:
        pass
    
    return info


async def detect_claude_code() -> HarnessInfo:
    """Detect Claude Code installation."""
    info = HarnessInfo(
        name="claude-code",
        display_name="Claude Code",
        command="claude",
        available=False,
    )
    
    claude_path = shutil.which("claude")
    if not claude_path:
        return info
    
    info.available = True
    info.command = claude_path
    
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [claude_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info.version = result.stdout.strip()
    except Exception:
        pass
    
    return info


async def detect_codex() -> HarnessInfo:
    """Detect Codex installation."""
    info = HarnessInfo(
        name="codex",
        display_name="Codex",
        command="codex",
        available=False,
    )
    
    codex_path = shutil.which("codex")
    if not codex_path:
        return info
    
    info.available = True
    info.command = codex_path
    
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [codex_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info.version = result.stdout.strip()
    except Exception:
        pass
    
    return info


async def detect_all_harnesses() -> list[HarnessInfo]:
    """Detect all available harnesses."""
    results = await asyncio.gather(
        detect_opencode(),
        detect_claude_code(),
        detect_codex(),
        return_exceptions=True,
    )
    
    harnesses = []
    for result in results:
        if isinstance(result, HarnessInfo) and result.available:
            harnesses.append(result)
    
    return harnesses


async def get_opencode_models() -> dict[str, list[str]]:
    """Get available models from OpenCode's provider config."""
    models = {}
    
    # Try opencode.json first (main config)
    config_paths = [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "opencode.jsonc",
    ]
    
    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                
                # OpenCode stores providers under "provider" key
                providers = config.get("provider", {})
                for provider_name, provider_config in providers.items():
                    if isinstance(provider_config, dict) and "models" in provider_config:
                        provider_models = provider_config["models"]
                        if isinstance(provider_models, dict):
                            model_names = list(provider_models.keys())
                            if model_names:
                                models[f"{provider_name}"] = model_names
            except Exception:
                continue
    
    return models


if __name__ == "__main__":
    async def main():
        harnesses = await detect_all_harnesses()
        print("Available harnesses:")
        for h in harnesses:
            print(f"  {h.display_name} ({h.name}): {h.version or 'unknown'} - {h.command}")
            if h.providers:
                print(f"    Providers: {', '.join(h.providers)}")
        
        models = await get_opencode_models()
        if models:
            print("\nOpenCode models by provider:")
            for provider, model_list in models.items():
                print(f"  {provider}: {', '.join(model_list)}")
    
    asyncio.run(main())
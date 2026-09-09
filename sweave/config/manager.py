from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .schemas import SweaveConfig, RoutingConfig, ModelsConfig, RoutingRule


class ConfigReloader(FileSystemEventHandler):
    """Watches config files and triggers reload callbacks."""
    
    def __init__(self, config_path: Path, callback: callable):
        self.config_path = config_path
        self.callback = callback
        self.observer = Observer()
        self.observer.schedule(self, config_path.parent, recursive=False)
    
    def on_modified(self, event):
        if event.src_path.endswith(self.config_path.name):
            self.callback()
    
    def start(self):
        self.observer.start()
    
    def stop(self):
        self.observer.stop()
        self.observer.join()


class ConfigManager:
    """Manages configuration with hot-reload support."""
    
    def __init__(self, config_path: Path = Path("config.yaml")):
        self.config_path = config_path
        self._config: SweaveConfig | None = None
        self._models_config: ModelsConfig | None = None
        self._routing_config: RoutingConfig | None = None
        self._reload_callbacks: list[callable] = []
        self._reloader: ConfigReloader | None = None
    
    def load(self) -> SweaveConfig:
        """Load configuration from YAML files."""
        # Load main config
        if self.config_path.exists():
            self._config = SweaveConfig.from_yaml(self.config_path)
        else:
            self._config = SweaveConfig()
        
        # Load models config
        models_path = Path(self._config.models.registry_path)
        if models_path.exists():
            with open(models_path, encoding="utf-8") as f:
                models_data = yaml.safe_load(f) or {}
            # Handle both formats: {models: {providers: {...}}} and {providers: {...}}.
            # The registry stores BARE model ids per provider; the
            # qualified "provider/model" form is built by get_all_models().
            models_dict = models_data.get("models") or models_data
            default = models_dict.get("default")
            providers_data = models_dict.get("providers") or {
                k: v for k, v in models_dict.items() if k != "default"
            }
            self._models_config = ModelsConfig(
                providers=providers_data, default=default
            )
        else:
            self._models_config = ModelsConfig()
        
        # Load routing config
        rules_path = Path(self._config.models.rules_path)
        if rules_path.exists():
            with open(rules_path, encoding="utf-8") as f:
                rules_data = yaml.safe_load(f) or {}
            self._routing_config = RoutingConfig(**rules_data)
        else:
            self._routing_config = RoutingConfig()
        
        # Merge into main config
        self._config.models.providers = self._models_config.providers
        self._config.routing = self._routing_config
        
        return self._config
    
    def get(self) -> SweaveConfig:
        """Get current configuration, loading if needed."""
        if self._config is None:
            return self.load()
        return self._config
    
    def get_models(self) -> ModelsConfig:
        """Get models configuration."""
        if self._models_config is None:
            self.load()
        return self._models_config
    
    @staticmethod
    def _qualify(provider: str, model: str) -> str:
        """Return the qualified ``provider/model`` form.

        Registry entries are bare model ids (e.g. ``qwen3:8b`` or
        ``@cf/...``); entries that already carry their provider prefix
        are returned unchanged (defensive: never double-qualify).
        """
        if "/" in model and model.split("/", 1)[0] == provider:
            return model
        return f"{provider}/{model}"

    def get_all_models(self) -> list[str]:
        """Get a flat list of all available models.

        Returns QUALIFIED ``provider/model`` ids (the form the opencode
        wire and the UI pickers expect). The registry stores bare ids
        per provider; qualification happens here, in exactly one place.
        """
        models = self.get_models()
        all_models = []
        for provider, provider_models in models.providers.items():
            for model in provider_models or []:
                all_models.append(self._qualify(provider, model))
        return all_models

    @staticmethod
    def _opencode_configured_model() -> str | None:
        """Return the model from the user's opencode.json, if set.

        This is the model the user's opencode serve is configured to
        use by default, so it is the most likely WORKING model. Read
        best-effort; any failure returns None (the registry fallback
        applies). The .jsonc variant may contain comments, so only the
        strict-JSON opencode.json is read.
        """
        import json

        config_path = Path.home() / ".config" / "opencode" / "opencode.json"
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError):
            return None
        model = config.get("model") if isinstance(config, dict) else None
        return model if isinstance(model, str) and "/" in model else None

    def get_default_model(self) -> str:
        """Get the default model (orchestrator + unset specialists).

        Precedence: models.yaml ``default`` (when it names a model in
        the registry) > the user's opencode.json ``model`` (the serve's
        own working default) > first ``opencode/`` model > first
        ``ollama/`` model > first ``gmi/`` model > first registry
        entry. The old behaviour (first registry entry, currently a
        cloudflare model most serves can't reach) 500s the chat turn,
        so the registry-first fallback is intentionally last.
        """
        all_models = self.get_all_models()
        available = set(all_models)
        configured_default = self.get_models().default
        if configured_default and configured_default in available:
            return configured_default
        opencode_model = self._opencode_configured_model()
        if opencode_model:
            return opencode_model
        for preferred in ("opencode", "ollama", "gmi", "openrouter"):
            for model in all_models:
                if model.split("/", 1)[0] == preferred:
                    return model
        return all_models[0] if all_models else "deepseek-flash"

    def set_default_model(self, model: str) -> str:
        """Persist a new default model to models.yaml.

        The model must be qualified (``provider/model``) and present
        in the registry — otherwise the next chat turn would 500 in
        the opencode serve. Raises ``ValueError`` on violation (the
        router maps this to a 400).
        """
        if "/" not in model:
            raise ValueError(
                f"model must be qualified as 'provider/model' (got {model!r})"
            )
        if model not in set(self.get_all_models()):
            raise ValueError(f"unknown model {model!r} (not in models.yaml registry)")
        models = self.get_models()
        models.default = model
        if self._config is not None:
            self._config.models.default = model
        self._persist_models()
        return model

    def _persist_models(self) -> None:
        """Write providers + default back to models.yaml (UTF-8, no BOM).

        yaml.safe_dump quotes entries that need it (e.g. ``@cf/...``
        ids), so no manual quoting is required. Never write via
        PowerShell redirection (UTF-16 LE + BOM breaks PyYAML).
        """
        models = self.get_models()
        registry_path = Path(
            self._config.models.registry_path if self._config else "models.yaml"
        )
        payload: dict[str, Any] = {"models": {"providers": models.providers}}
        if models.default:
            payload["models"]["default"] = models.default
        with open(registry_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    
    def resolve_model(self, role: str, override: str | None = None) -> str:
        """Resolve model for a role, with optional override.
        
        Now ignores role and returns the default model unless overridden.
        """
        if override:
            return override
        return self.get_default_model()
    
    def register_reload_callback(self, callback: callable):
        """Register a callback to be called on config reload."""
        self._reload_callbacks.append(callback)
    
    def enable_hot_reload(self):
        """Enable hot-reload for config files."""
        if self._config and self._config.models.hot_reload:
            if self._reloader is None:
                self._reloader = ConfigReloader(self.config_path, self._on_reload)
                self._reloader.start()
    
    def disable_hot_reload(self):
        """Disable hot-reload."""
        if self._reloader:
            self._reloader.stop()
            self._reloader = None
    
    def _on_reload(self):
        """Handle config file reload."""
        old_config = self._config
        self.load()
        for callback in self._reload_callbacks:
            try:
                callback(old_config, self._config)
            except Exception:
                pass  # Log in production
    
    def add_routing_rule(self, pattern: str, agent: str, model: str | None = None) -> None:
        """Add a routing rule and persist."""
        routing = self.get_routing()
        routing.routes.append(RoutingRule(pattern=pattern, agent=agent, model=model))
        
        # Persist to rules.yaml
        rules_path = Path(self._config.models.rules_path)
        with open(rules_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(routing.model_dump(exclude_none=True), f, sort_keys=False)
    
    def get_routing(self) -> RoutingConfig:
        """Get routing configuration."""
        if self._routing_config is None:
            self.load()
        return self._routing_config
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

        # Customs layer (models.custom.yaml, hand-maintained): entries
        # neither models.dev nor the serve knows — local-only models,
        # extra variant rows, project-specific additions. Additive and
        # never overwritten by `sweave models sync`.
        customs_default = self._merge_custom_registry(models_path)
        if customs_default:
            self._models_config.default = customs_default
        
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

    def _merge_custom_registry(self, models_path: Path) -> str | None:
        """Merge models.custom.yaml into the loaded registry (additive).

        Returns a default override when the customs file sets one,
        else None. Missing/invalid customs file -> no-op (never fail
        startup over the optional layer).
        """
        from sweave.models_sync import CUSTOMS_FILENAME, merge_custom_rows

        customs_path = models_path.parent / CUSTOMS_FILENAME
        if not customs_path.exists():
            return None
        try:
            with open(customs_path, encoding="utf-8") as f:
                customs_data = yaml.safe_load(f) or {}
        except Exception:
            return None
        customs_models = customs_data.get("models") or customs_data
        if not isinstance(customs_models, dict):
            return None
        merged = merge_custom_rows(
            dict(self._models_config.providers), customs_models
        )
        self._models_config.providers = merged
        default = customs_models.get("default")
        return default if isinstance(default, str) and default else None

    def get_model_meta(self, qualified_id: str | None = None) -> dict:
        """Return metadata for a qualified model id (or the whole map).

        Reads the models.meta.json sidecar written by `sweave models
        sync` (variants, reasoning options, limits, modalities, cost).
        Absent sidecar -> {} (utilities must treat metadata as
        best-effort, never load-bearing).
        """
        import json

        from sweave.models_sync import META_FILENAME

        sidecar: dict = {}
        try:
            registry_path = Path(
                self._config.models.registry_path if self._config else "models.yaml"
            )
            meta_path = registry_path.parent / META_FILENAME
            if meta_path.exists():
                with open(meta_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    sidecar = loaded
        except Exception:
            sidecar = {}
        if qualified_id is None:
            return sidecar
        entry = sidecar.get(qualified_id)
        return entry if isinstance(entry, dict) else {}
    
    @staticmethod
    def _qualify(provider: str, model: str) -> str:
        """Return the qualified ``provider/model`` form.

        Always prefixes: registry rows are BARE model ids, even when
        a bare id itself starts with ``provider/`` (nvidia and
        openrouter list models like ``nvidia/active-speaker`` /
        ``openrouter/auto`` — those prefixes are part of the model
        id, and the qualified form is the doubled
        ``nvidia/nvidia/active-speaker``). The old defensive
        passthrough (skip when the row "already" starts with the
        provider) silently produced single-prefixed ids whose wire
        modelID no longer matched the serve's model key.
        """
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
        if configured_default and self._is_selectable_model(configured_default, available):
            return configured_default
        opencode_model = self._opencode_configured_model()
        if opencode_model:
            return opencode_model
        for preferred in ("opencode", "ollama", "gmi", "openrouter"):
            for model in all_models:
                if model.split("/", 1)[0] == preferred:
                    return model
        return all_models[0] if all_models else "deepseek-flash"

    def _is_selectable_model(self, model: str, available: set[str]) -> bool:
        """Mirror of :meth:`set_default_model` validation for reads.

        The stored default may carry a ``+variant`` suffix the
        registry no longer lists as a row (variants are a dropdown
        dimension, not registry entries); accept it when the base
        is registered and the variant is advertised (or unknown to
        the metadata sidecar).
        """
        from sweave.runtime.specialist_store import parse_model_ref

        if model in available:
            return True
        ref = parse_model_ref(model)
        if not ref or not ref.get("provider") or not ref.get("model_id"):
            return False
        base = f"{ref['provider']}/{ref['model_id']}"
        if base not in available:
            return False
        variant = ref.get("variant")
        if not variant:
            return True
        known = self.get_model_meta(base).get("variants") or []
        return not known or variant in known

    def set_default_model(self, model: str) -> str:
        """Persist a new default model to models.yaml.

        Accepts qualified ``provider/model`` ids and
        ``provider/model+variant`` effort selections (the effort
        dropdown builds these; the registry lists base rows). A
        variant must be advertised for its model when the metadata
        sidecar knows the model — otherwise the next chat turn
        would 500 in the opencode serve. Raises ``ValueError`` on
        violation (the router maps this to a 400).
        """
        from sweave.runtime.specialist_store import parse_model_ref

        if "/" not in model:
            raise ValueError(
                f"model must be qualified as 'provider/model' (got {model!r})"
            )
        available = set(self.get_all_models())
        if model in available:
            resolved_variant: str | None = None
        else:
            ref = parse_model_ref(model)
            if not ref or not ref.get("provider") or not ref.get("model_id"):
                raise ValueError(f"unknown model {model!r} (not in models.yaml registry)")
            base = f"{ref['provider']}/{ref['model_id']}"
            if base not in available:
                raise ValueError(f"unknown model {model!r} (not in models.yaml registry)")
            resolved_variant = ref.get("variant")
            if resolved_variant:
                known = self.get_model_meta(base).get("variants") or []
                if known and resolved_variant not in known:
                    raise ValueError(
                        f"unknown variant {resolved_variant!r} for {base} "
                        f"(advertised: {', '.join(known)})"
                    )
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
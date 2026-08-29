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
            with open(models_path) as f:
                models_data = yaml.safe_load(f) or {}
            # Handle both formats: {models: {roles: {...}}} and {roles: {...}}
            models_dict = models_data.get("models") or models_data.get("roles") or {}
            # If models_dict has extra fields (registry_path, etc.), extract just roles
            if isinstance(models_dict, dict) and "roles" in models_dict:
                roles_data = models_dict["roles"]
            else:
                roles_data = models_dict
            self._models_config = ModelsConfig(roles=roles_data)
        else:
            self._models_config = ModelsConfig()
        
        # Load routing config
        rules_path = Path(self._config.models.rules_path)
        if rules_path.exists():
            with open(rules_path) as f:
                rules_data = yaml.safe_load(f) or {}
            self._routing_config = RoutingConfig(**rules_data)
        else:
            self._routing_config = RoutingConfig()
        
        # Merge into main config
        self._config.models.roles = self._models_config.roles
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
    
    def get_routing(self) -> RoutingConfig:
        """Get routing configuration."""
        if self._routing_config is None:
            self.load()
        return self._routing_config
    
    def resolve_model(self, role: str, override: str | None = None) -> str:
        """Resolve model for a role, with optional override."""
        if override:
            return override
        
        models = self.get_models()
        if role in models.roles:
            return models.roles[role].default
        
        # Fallback to orchestrator model
        if "orchestrator" in models.roles:
            return models.roles["orchestrator"].default
        
        return "deepseek-flash"  # Ultimate fallback
    
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
    
    def update_model(self, role: str, model: str) -> None:
        """Update model for a role and persist."""
        models = self.get_models()
        if role not in models.roles:
            from .schemas import ModelRoleConfig
            models.roles[role] = ModelRoleConfig(default=model)
        else:
            models.roles[role].default = model
        
        # Persist to models.yaml - only save the roles dict
        models_path = Path(self._config.models.registry_path)
        roles_data = {k: v.model_dump(exclude_none=True) for k, v in models.roles.items()}
        data = {"models": roles_data}
        with open(models_path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    
    def add_routing_rule(self, pattern: str, agent: str, model: str | None = None) -> None:
        """Add a routing rule and persist."""
        routing = self.get_routing()
        routing.routes.append(RoutingRule(pattern=pattern, agent=agent, model=model))
        
        # Persist to rules.yaml
        rules_path = Path(self._config.models.rules_path)
        with open(rules_path, "w") as f:
            yaml.safe_dump(routing.model_dump(exclude_none=True), f, sort_keys=False)
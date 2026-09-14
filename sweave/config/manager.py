from __future__ import annotations

from pathlib import Path
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .schemas import SweaveConfig, RoutingConfig, ModelsConfig, RoutingRule, HarnessSettings


def _set_models_default_line(text: str, scalar: str) -> str | None:
    """Splice ``default: <scalar>`` into the top-level ``models:`` block.

    Returns the edited text, or None when the file has no anchorable
    block (an inline ``models: {...}`` or no ``models:`` key at all —
    the caller falls back to a YAML rewrite). Comment- and
    key-preserving: every other line is byte-identical, including the
    original indent style of an existing ``default:`` line.
    """
    import re

    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"^models:\s*(#.*)?$", ln)),
        None,
    )
    if start is None:
        return None
    # The block is the run of indented/blank lines after `models:`.
    # An inline value (`models: {...}`) never matches the anchor
    # regex above, so reaching here with a scalar on the anchor line
    # is impossible — but guard anyway.
    end = start + 1
    while end < len(lines) and (
        lines[end].strip() == "" or lines[end][:1] in (" ", "\t")
    ):
        existing = re.match(r"^(\s*)default\s*:.*$", lines[end])
        if existing:
            lines[end] = f"{existing.group(1)}default: {scalar}\n"
            return "".join(lines)
        end += 1
    lines.insert(start + 1, f"  default: {scalar}\n")
    return "".join(lines)


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
        """Load configuration from YAML files.

        User-default home (fast-track 2026-09-11): the user's model
        selection lives in ``config.yaml`` (``models.default``), NOT in
        the generated registry. Stored-default precedence:
        config.yaml ``default`` > ``models.custom.yaml`` overlay >
        legacy ``models.yaml`` ``default`` (adopted once, below).
        """
        # Load main config
        if self.config_path.exists():
            self._config = SweaveConfig.from_yaml(self.config_path)
        else:
            self._config = SweaveConfig()
        file_default = self._config.models.default

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

        # Fast-track migration (once, idempotent): a legacy
        # models.yaml ``default`` with no config.yaml home is adopted
        # into config.yaml — but never from under a live customs
        # layer (customs stays dynamic; freezing it into config would
        # silently pin the user's hand-maintained file, and a later
        # customs ``default`` edit is shadowed by an adopted config
        # value by the precedence above). The models.yaml key is left
        # in place but henceforth ignored — no destructive rewrite of
        # user data. No file is ever created from load(): a missing
        # config file adopts in memory only.
        registry_default = self._models_config.default
        if (
            not file_default
            and customs_default is None
            and registry_default
            and self.config_path.exists()
            and self._is_selectable_model(
                registry_default, set(self.get_all_models())
            )
        ):
            file_default = registry_default
            self._persist_config_default(registry_default)

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
        self._config.models.default = file_default or registry_default
        self._models_config.default = self._config.models.default
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

        Precedence (fast-track 2026-09-11 — the STORED default is the
        first applicable of the first three; the rest are fallbacks
        when it is unset or names a model no longer in the registry):
        config.yaml ``models.default`` (the user's selection) >
        ``models.custom.yaml`` overlay > legacy ``models.yaml``
        ``default`` (adopted into config on load when selectable) >
        the user's opencode.json ``model`` (the serve's own working
        default) > first ``opencode/`` model > first ``ollama`` /
        ``gmi`` / ``openrouter`` model > first registry entry. The old
        behaviour (first registry entry, currently a cloudflare model
        most serves can't reach) 500s the chat turn, so the
        registry-first fallback is intentionally last.
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
        """Persist a new default model to config.yaml.

        Accepts qualified ``provider/model`` ids and
        ``provider/model+variant`` effort selections (the effort
        dropdown builds these; the registry lists base rows). A
        variant must be advertised for its model when the metadata
        sidecar knows the model — otherwise the next chat turn
        would 500 in the opencode serve. Raises ``ValueError`` on
        violation (the router maps this to a 400).

        M1.13 step 3 (ruling 2026-09-10): the STORED default stays
        BARE — no ``+variant`` suffix in the file. The env-form
        ``OPENCODE_MODEL`` must be bare (a suffix there 500s every
        serve turn: harness/opencode.py:980-988, live probe
        2026-09-10); effort variants flow as the structured v2 body
        field, never through the stored default.

        Fast-track 2026-09-11: the home is config.yaml
        (``models.default``); models.yaml is never touched — it is a
        generated artifact, and user state + generated artifact in
        one file was the three-writer clobber bug.
        """
        from sweave.runtime.specialist_store import parse_model_ref

        if "/" not in model:
            raise ValueError(
                f"model must be qualified as 'provider/model' (got {model!r})"
            )
        available = set(self.get_all_models())
        if model in available:
            bare = model
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
            # Strip the variant: the STORED default stays bare (see
            # docstring) — the suffix is validated above, then dropped.
            bare = base
        models = self.get_models()
        models.default = bare
        if self._config is not None:
            self._config.models.default = bare
        self._persist_config_default(bare)
        # config.yaml edits fire the ConfigReloader too (it watches
        # config.yaml — see ConfigReloader.schedule/on_modified), but
        # the watchdog is async and racy: reload synchronously + fan
        # out to the registered callbacks now, the same contract the
        # file-watch path invokes (a later watchdog reload is a
        # harmless idempotent no-op).
        self._sync_reload()
        return bare

    def _persist_config_default(self, value: str) -> None:
        """Write the bare user default into config.yaml (surgical).

        Line-preserving edit inside the top-level ``models:`` block so
        comments and unrelated keys survive byte-identical (a PyYAML
        round-trip would strip them); falls back to a full YAML
        rewrite when the file has no ``models:`` block to anchor on,
        and to a minimal ``{models: {default}}`` document when the
        config file does not exist yet (schema defaults fill the rest
        on the next load — never dump merged registry data here).

        The write is atomic (tmp + os.replace). Concurrent-writer
        locking is out of scope (filed per the plan risk note).

        Never touches models.yaml (fast-track 2026-09-11). Never
        write via PowerShell redirection (UTF-16 LE + BOM breaks
        PyYAML).
        """
        import re

        import yaml

        from sweave.runtime.locking import atomic_write_text_sync

        # Quote iff the value needs it. NOTE: never splice
        # ``yaml.safe_dump(value)`` for a bare scalar straight into a
        # larger document — PyYAML appends a ``...`` document-end
        # marker (``"<v>\n...\n"``) that ``.strip()`` does NOT remove
        # (2026-09-11: this exact splice broke config.yaml mid-suite).
        if re.match(r"^[A-Za-z0-9][A-Za-z0-9_/.:+@-]*$", value):
            scalar = value
        else:  # pragma: no cover - validated defaults are plain-safe
            scalar = yaml.safe_dump(value, allow_unicode=True).splitlines()[0]
        path = Path(self.config_path)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            edited = _set_models_default_line(text, scalar)
            if edited is not None:
                atomic_write_text_sync(path, edited)
                return
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
        models = data.get("models")
        if not isinstance(models, dict):
            models = {}
            data["models"] = models
        models["default"] = value
        atomic_write_text_sync(
            path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        )

    def _sync_reload(self) -> None:
        """Programmatic hot-reload after a registry write.

        The ConfigReloader (manager.py:12-30) only fires on
        ``config.yaml`` edits; a models.yaml-only write NEVER
        triggers the registered reload callbacks, so the running
        server kept the boot-time default until restart or a manual
        config.yaml mtime touch (2026-09-10 live probe: POST
        /api/models changed the file, the next chat turn still
        routed on the stale model). This reloads in-process and
        invokes every registered callback with the (old, new) pair
        — identical contract to :meth:`_on_reload`.
        """
        old_config = self._config
        self.load()
        for callback in self._reload_callbacks:
            try:
                callback(old_config, self._config)
            except Exception:
                pass  # Log in production

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

    def _read_project_overlay(self, project_dir: Path | None) -> dict:
        """Parse the project overlay file, or {} when absent/unusable.

        Never raises: missing file, bad YAML, or non-dict docs all
        mean "no overlay" (warning-logged, except the missing case).
        """
        import logging

        if project_dir is None:
            return {}
        try:
            path = Path(project_dir) / ".sweave" / PROJECT_CONFIG_FILENAME
        except Exception:  # noqa: BLE001
            return {}
        try:
            if not path.exists():
                return {}
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Project config overlay unreadable (%s); using global: %s",
                path, exc,
            )
            return {}
        if not isinstance(data, dict):
            logging.getLogger(__name__).warning(
                "Project config overlay not a mapping (%s); using global", path
            )
            return {}
        return data

    def overlay_sections_for(
        self, project_dir: Path | str | None
    ) -> list[str]:
        """Sorted overlay section names that are mappings (provenance
        for the effective-config endpoint: which layers the project
        actually provides)."""
        overlay = self._read_project_overlay(
            Path(project_dir) if project_dir is not None else None
        )
        return sorted(k for k, v in overlay.items() if isinstance(v, dict))

    def get_for_project(self, project_dir: Path | str | None) -> SweaveConfig:
        """Effective config for a project: global + project overlay.

        ``None`` (or no overlay file) returns the global config
        unchanged. Only :data:`OVERRIDABLE_SECTIONS` merge, field by
        field; anything else in the overlay is ignored with a warning.
        Section values that fail validation fall back to the global
        section loudly (warning), never failing the caller.
        """
        import logging

        base = self.get()
        overlay = self._read_project_overlay(
            Path(project_dir) if project_dir is not None else None
        )
        if not overlay:
            return base
        merged = base.model_copy(deep=True)
        section_models = {
            "models": ModelsConfig,
            "routing": RoutingConfig,
            "harness": HarnessSettings,
        }
        for key, value in overlay.items():
            if key not in OVERRIDABLE_SECTIONS:
                if key in ("server", "memory", "git"):
                    logging.getLogger(__name__).warning(
                        "Project overlay section %r is process-global and "
                        "ignored (project %s)",
                        key, project_dir,
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "Project overlay has unknown section %r; ignored "
                        "(project %s)", key, project_dir,
                    )
                continue
            if not isinstance(value, dict):
                logging.getLogger(__name__).warning(
                    "Project overlay section %r must be a mapping; ignored "
                    "(project %s)", key, project_dir,
                )
                continue
            try:
                current = getattr(merged, key).model_dump()
                current.update(value)
                setattr(merged, key, section_models[key](**current))
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "Project overlay section %r invalid (%s); using global "
                    "(project %s)", key, exc, project_dir,
                )
                continue
        return merged

    def get_routing_for_project(
        self, project_dir: Path | str | None
    ) -> RoutingConfig:
        """Effective routing for a project (overlay-aware)."""
        if project_dir is None:
            return self.get_routing()
        return self.get_for_project(project_dir).routing

    def get_default_model_for_project(
        self, project_dir: Path | str | None
    ) -> str:
        """Default model with the project overlay applied.

        Same precedence as :meth:`get_default_model`, but the stored
        default is read from the merged ``models`` section (project
        ``models.default`` wins when selectable; otherwise the global
        chain applies unchanged).
        """
        if project_dir is None:
            return self.get_default_model()
        effective = self.get_for_project(project_dir)
        fallback = self.get_default_model()
        candidate = effective.models.default
        if candidate:
            try:
                available = set(self.get_all_models())
            except Exception:  # noqa: BLE001
                available = set()
            if available and self._is_selectable_model(candidate, available):
                return candidate
            if not available and candidate:
                return candidate
        return fallback

    def resolve_model(
        self,
        role: str,
        override: str | None = None,
        project_dir: Path | str | None = None,
    ) -> str:
        """Resolve model for a role, with optional override.

        Now ignores role and returns the default model unless overridden
        (kept). ``project_dir`` selects the project overlay for the
        default; ``None`` keeps the global behavior exactly.
        """
        if override:
            return override
        if project_dir is None:
            return self.get_default_model()
        return self.get_default_model_for_project(project_dir)

    # -- Per-project layer (user ruling: two files) ----------------------
    #
    # Global ``config.yaml`` holds defaults for every project. A project
    # may overlay ``{project_dir}/.sweave/config.yaml`` with any subset
    # of the OVERRIDABLE_SECTIONS below; present fields win per field,
    # absent fields inherit global. ``server`` / ``memory`` / ``git``
    # are process-global and never overridable (a project file naming
    # them is ignored with a warning, never an error). Unknown
    # top-level keys are ignored the same way (forward compatibility).
    #
    # The overlay is read fresh on every resolution (one small YAML
    # read per turn when the file exists, one stat when it does not) —
    # no cache to invalidate, so project edits apply without restart
    # or watcher. A malformed overlay falls back to global loudly
    # (warning log), never failing the turn.


#: Project overlay filename (inside the project's ``.sweave/`` dir,
# alongside ``agents.json`` / ``delegations.json``).
PROJECT_CONFIG_FILENAME = "config.yaml"

#: Top-level sections a project file may override (field-level merge).
OVERRIDABLE_SECTIONS = ("models", "routing", "harness")
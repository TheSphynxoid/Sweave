from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sweave.config.manager import ConfigManager
from sweave.config.schemas import RoutingRule


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    agent: str
    model: str | None
    matched_rule: RoutingRule | None
    confidence: float
    reasoning: str


class RuleRouter:
    """Routes tasks to agents based on rules.yaml with LLM fallback."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
    
    def route(self, task: str) -> RoutingDecision:
        """Route a task to an agent using rules."""
        routing_config = self.config.get_routing()
        
        # Try rule-based matching first
        for rule in routing_config.routes:
            if self._match_pattern(task, rule.pattern):
                model = rule.model
                if model and "{{" in model:
                    # Resolve template
                    model = self._resolve_template(model)
                
                return RoutingDecision(
                    agent=rule.agent,
                    model=model,
                    matched_rule=rule,
                    confidence=0.9,
                    reasoning=f"Matched rule pattern: {rule.pattern}",
                )
        
        # Fallback to LLM-based routing
        return self._llm_fallback(task)
    
    def _match_pattern(self, text: str, pattern: str) -> bool:
        """Match text against pattern (regex or keyword)."""
        # Try as regex first
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            pass
        
        # Fallback to keyword matching (comma-separated)
        keywords = [k.strip().lower() for k in pattern.split(",")]
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)
    
    def _resolve_template(self, template: str) -> str:
        """Resolve template variables like {{models.backend.default}}."""
        import re as regex
        
        def replace(match):
            var = match.group(1).strip()
            # Navigate config: models.roles.backend.default
            parts = var.split(".")
            obj = self.config.get()
            for part in parts:
                if part == "models":
                    # models -> models.roles
                    if hasattr(obj, "models"):
                        obj = obj.models
                    elif hasattr(obj, "roles"):
                        obj = obj.roles
                    else:
                        return match.group(0)
                elif part == "roles":
                    if hasattr(obj, "roles"):
                        obj = obj.roles
                    else:
                        return match.group(0)
                elif hasattr(obj, part):
                    obj = getattr(obj, part)
                elif isinstance(obj, dict) and part in obj:
                    obj = obj[part]
                elif hasattr(obj, "roles") and part in obj.roles:
                    # Special case: accessing role directly from models config
                    obj = obj.roles[part]
                else:
                    return match.group(0)
            return str(obj) if obj else match.group(0)
        
        return regex.sub(r"\{\{\s*(.+?)\s*\}\}", replace, template)
    
    def _llm_fallback(self, task: str) -> RoutingDecision:
        """LLM-based routing fallback (placeholder for actual LLM call)."""
        # In production, this would call the orchestrator LLM
        # For now, use simple heuristics
        task_lower = task.lower()
        
        if any(kw in task_lower for kw in ["backend", "api", "database", "server", "sql", "auth", "orm"]):
            return RoutingDecision(
                agent="backend",
                model=self.config.resolve_model("backend"),
                matched_rule=None,
                confidence=0.7,
                reasoning="LLM fallback: backend keywords detected",
            )
        
        if any(kw in task_lower for kw in ["frontend", "ui", "react", "css", "component", "html", "vue", "svelte"]):
            return RoutingDecision(
                agent="frontend",
                model=self.config.resolve_model("frontend"),
                matched_rule=None,
                confidence=0.7,
                reasoning="LLM fallback: frontend keywords detected",
            )
        
        if any(kw in task_lower for kw in ["review", "audit", "security", "lint", "check"]):
            return RoutingDecision(
                agent="reviewer",
                model=self.config.resolve_model("reviewer"),
                matched_rule=None,
                confidence=0.7,
                reasoning="LLM fallback: review keywords detected",
            )
        
        # Default to backend
        return RoutingDecision(
            agent="backend",
            model=self.config.resolve_model("backend"),
            matched_rule=None,
            confidence=0.5,
            reasoning="LLM fallback: default to backend",
        )
    
    def add_rule(self, pattern: str, agent: str, model: str | None = None):
        """Add a routing rule dynamically."""
        self.config.add_routing_rule(pattern, agent, model)
    
    def get_rules(self) -> list[RoutingRule]:
        """Get current routing rules."""
        return self.config.get_routing().routes
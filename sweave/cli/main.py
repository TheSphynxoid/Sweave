from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from sweave.config.manager import ConfigManager
from sweave.config.schemas import SweaveConfig
from sweave.harness.base import harness_registry, Message
from sweave.router.router import RuleRouter
from sweave.workspace.manager import WorktreeManager
from sweave.tools import (
    RouteTaskTool,
    DelegateTaskTool,
    WorktreeTool,
    MemoryTool,
)
from sweave.memory.backends import MemoryFactory

app = typer.Typer(
    name="sweave",
    help="Multi-agent orchestration platform with persistent specialist agents",
    add_completion=False,
)

console = Console()
config_manager = ConfigManager()


@app.callback()
def callback(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c", help="Config file path"),
):
    """Sweave - Multi-agent orchestration platform."""
    config_manager.config_path = config
    config_manager.load()


@app.command()
def run(
    task: str = typer.Argument(..., help="Task to execute"),
    agent: str = typer.Option(None, "--agent", "-a", help="Force specific agent"),
    model: str = typer.Option(None, "--model", "-m", help="Override model for this task"),
):
    """Run a task through the orchestrator."""
    config = config_manager.get()
    console.print(Panel(f"[bold]Task:[/bold] {task}", title="Sweave Run"))
    
    asyncio.run(_run_task(task, agent, model))


async def _run_task(task: str, agent_override: str | None, model_override: str | None):
    """Execute a task asynchronously."""
    # Initialize tools
    router = RuleRouter(config_manager)
    worktree_manager = WorktreeManager(config.git.worktree_base)
    delegate_tool = DelegateTaskTool(config_manager, worktree_manager)
    
    # Route task
    if agent_override:
        decision = router._llm_fallback(task)
        decision.agent = agent_override
        decision.model = model_override or config_manager.resolve_model(agent_override)
        console.print(f"[yellow]Forced agent:[/yellow] {agent_override}")
    else:
        decision = router.route(task)
    
    console.print(f"[green]Routing:[/green] {decision.agent} ({decision.model}) - {decision.reasoning}")
    
    # Delegate task
    result = await delegate_tool.execute(
        agent=decision.agent,
        task=task,
        model=model_override or decision.model,
    )
    
    if result.success:
        console.print(Panel(result.output, title=f"[green]Result from {result.agent}[/green]"))
    else:
        console.print(Panel(result.error or "Unknown error", title=f"[red]Error in {result.agent}[/red]", border_style="red"))


@app.command()
def models(
    list_: bool = typer.Option(False, "--list", "-l", help="List current models"),
    set_: str = typer.Option(None, "--set", "-s", help="Set model (format: role=model)"),
    reset: bool = typer.Option(False, "--reset", help="Reset to defaults"),
):
    """Manage model configuration."""
    if list_:
        _list_models()
    elif set_:
        _set_model(set_)
    elif reset:
        _reset_models()
    else:
        _list_models()


def _list_models():
    """List current model configuration."""
    config = config_manager.get_models()
    
    table = Table(title="Model Configuration")
    table.add_column("Role", style="cyan")
    table.add_column("Default Model", style="green")
    table.add_column("Aliases", style="yellow")
    table.add_column("Provider", style="blue")
    
    for role, role_config in config.roles.items():
        table.add_row(
            role,
            role_config.default,
            ", ".join(role_config.aliases) if role_config.aliases else "-",
            role_config.provider,
        )
    
    console.print(table)


def _set_model(value: str):
    """Set model for a role."""
    if "=" not in value:
        console.print("[red]Format: role=model[/red]")
        return
    
    role, model = value.split("=", 1)
    config_manager.update_model(role.strip(), model.strip())
    console.print(f"[green]Updated {role} -> {model}[/green]")


def _reset_models():
    """Reset models to defaults."""
    # Would regenerate from models.yaml
    console.print("[yellow]Reset not yet implemented[/yellow]")


@app.command()
def worktree(
    list_: bool = typer.Option(False, "--list", "-l", help="List worktrees"),
    clean: bool = typer.Option(False, "--clean", help="Clean merged worktrees"),
    pr: str = typer.Option(None, "--pr", help="Create PR for task (format: task_id:agent)"),
):
    """Manage git worktrees."""
    worktree_manager = WorktreeManager(config_manager.get().git.worktree_base)
    
    if list_:
        _list_worktrees(worktree_manager)
    elif clean:
        _clean_worktrees(worktree_manager)
    elif pr:
        _create_pr(worktree_manager, pr)
    else:
        _list_worktrees(worktree_manager)


def _list_worktrees(manager: WorktreeManager):
    """List all worktrees."""
    worktrees = manager.list_worktrees()
    
    table = Table(title="Active Worktrees")
    table.add_column("Path", style="cyan")
    table.add_column("Branch", style="green")
    table.add_column("Task ID", style="yellow")
    table.add_column("Agent", style="blue")
    
    for wt in worktrees:
        table.add_row(str(wt.path), wt.branch, wt.task_id, wt.agent_name)
    
    console.print(table)


def _clean_worktrees(manager: WorktreeManager):
    """Clean merged worktrees."""
    worktrees = manager.list_worktrees()
    for wt in worktrees:
        # In production, check if PR is merged
        manager.remove_worktree(wt.task_id, wt.agent_name, force=True)
    console.print(f"[green]Cleaned {len(worktrees)} worktrees[/green]")


def _create_pr(manager: WorktreeManager, value: str):
    """Create PR for a worktree."""
    if ":" not in value:
        console.print("[red]Format: task_id:agent[/red]")
        return
    
    task_id, agent = value.split(":", 1)
    config = config_manager.get()
    
    url = manager.create_pr(
        task_id=task_id,
        agent_name=agent,
        title=f"Sweave: {task_id} - {agent}",
        body=f"Automated PR from Sweave task {task_id}",
        base_branch=config.git.pr_base_branch,
        token=config.git.token,
    )
    
    if url:
        console.print(f"[green]PR created:[/green] {url}")
    else:
        console.print("[red]Failed to create PR[/red]")


@app.command()
def memory(
    init: bool = typer.Option(False, "--init", help="Initialize memory backend"),
    recall: str = typer.Option(None, "--recall", "-r", help="Recall memories"),
    reflect: str = typer.Option(None, "--reflect", help="Reflect on memories"),
    mode: str = typer.Option("embedded_slim", "--mode", help="Hindsight mode"),
):
    """Manage long-term memory."""
    if init:
        _init_memory(mode)
    elif recall:
        _recall_memory(recall)
    elif reflect:
        _reflect_memory(reflect)
    else:
        console.print("Use --init, --recall, or --reflect")


def _init_memory(mode: str):
    """Initialize memory backend."""
    config = config_manager.get()
    config.memory.hindsight.mode = mode  # type: ignore
    config_manager.enable_hot_reload()
    
    memory = MemoryFactory.create(config.memory)
    asyncio.run(_check_memory(memory))
    console.print(f"[green]Memory backend initialized: {mode}[/green]")


async def _check_memory(memory):
    """Check memory backend health."""
    healthy = await memory.health_check()
    if healthy:
        console.print("[green]Memory backend healthy[/green]")
    else:
        console.print("[yellow]Memory backend check failed[/yellow]")


def _recall_memory(query: str):
    """Recall memories."""
    config = config_manager.get()
    memory = MemoryFactory.create(config.memory)
    bank_id = config.memory.hindsight.bank_id
    
    results = asyncio.run(memory.recall(query, bank_id))
    
    if results:
        for i, mem in enumerate(results):
            console.print(Panel(mem.content, title=f"Memory {i+1}"))
    else:
        console.print("[yellow]No memories found[/yellow]")


def _reflect_memory(query: str):
    """Reflect on memories."""
    config = config_manager.get()
    memory = MemoryFactory.create(config.memory)
    bank_id = config.memory.hindsight.bank_id
    
    result = asyncio.run(memory.reflect(query, bank_id))
    console.print(Panel(result, title="Reflection"))


@app.command()
def route(
    task: str = typer.Argument(..., help="Task to route"),
):
    """Show routing decision for a task."""
    router = RuleRouter(config_manager)
    decision = router.route(task)
    
    console.print(Panel(
        f"Agent: [cyan]{decision.agent}[/cyan]\n"
        f"Model: [green]{decision.model}[/green]\n"
        f"Confidence: [yellow]{decision.confidence:.0%}[/yellow]\n"
        f"Reasoning: {decision.reasoning}",
        title="Routing Decision",
    ))


@app.command()
def rules(
    list_: bool = typer.Option(False, "--list", "-l", help="List routing rules"),
    add: str = typer.Option(None, "--add", help="Add rule (pattern|agent|model)"),
):
    """Manage routing rules."""
    if list_:
        _list_rules()
    elif add:
        _add_rule(add)
    else:
        _list_rules()


def _list_rules():
    """List routing rules."""
    routing = config_manager.get_routing()
    
    table = Table(title="Routing Rules")
    table.add_column("#", style="dim")
    table.add_column("Pattern", style="cyan")
    table.add_column("Agent", style="green")
    table.add_column("Model", style="yellow")
    
    for i, rule in enumerate(routing.routes):
        table.add_row(str(i+1), rule.pattern, rule.agent, rule.model or "-")
    
    table.add_row("-", f"fallback: {routing.fallback}", "-", "-")
    console.print(table)


def _add_rule(value: str):
    """Add a routing rule."""
    parts = value.split("|")
    if len(parts) < 2:
        console.print("[red]Format: pattern|agent|model[/red]")
        return
    
    pattern = parts[0].strip()
    agent = parts[1].strip()
    model = parts[2].strip() if len(parts) > 2 else None
    
    config_manager.add_routing_rule(pattern, agent, model)
    console.print(f"[green]Added rule:[/green] {pattern} -> {agent} ({model or 'default'})")


@app.command()
def doctor():
    """Check system dependencies."""
    console.print("[bold]Sweave Doctor[/bold]\n")
    
    checks = [
        ("Python >=3.11", True),
        ("OpenCode", _check_opencode()),
        ("Git", _check_git()),
        ("gh CLI", _check_gh()),
        ("Docker", _check_docker()),
        ("Hindsight (embedded)", _check_hindsight_embedded()),
    ]
    
    table = Table()
    table.add_column("Dependency", style="cyan")
    table.add_column("Status", style="green")
    
    for name, status in checks:
        table.add_row(name, "[green]OK[/green]" if status else "[red]FAIL[/red]")
    
    console.print(table)


def _check_opencode() -> bool:
    try:
        import subprocess
        result = subprocess.run(["opencode", "--version"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _check_git() -> bool:
    try:
        import subprocess
        result = subprocess.run(["git", "--version"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _check_gh() -> bool:
    try:
        import subprocess
        result = subprocess.run(["gh", "--version"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _check_docker() -> bool:
    try:
        import subprocess
        result = subprocess.run(["docker", "--version"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _check_hindsight_embedded() -> bool:
    try:
        import hindsight_client
        return True
    except ImportError:
        return False


@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Show current config"),
    validate: bool = typer.Option(False, "--validate", help="Validate config"),
):
    """Manage configuration."""
    if show:
        _show_config()
    elif validate:
        _validate_config()
    else:
        _show_config()


def _show_config():
    """Show current configuration."""
    config = config_manager.get()
    import yaml
    console.print(Syntax(yaml.safe_dump(config.model_dump(exclude_none=True)), "yaml"))


def _validate_config():
    """Validate configuration."""
    try:
        config = config_manager.get()
        console.print("[green]Configuration valid[/green]")
    except Exception as e:
        console.print(f"[red]Configuration invalid:[/red] {e}")


@app.command()
def web(
    host: str = typer.Option(None, "--host", help="Host to bind to"),
    port: int = typer.Option(None, "--port", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """Start the Sweave web server."""
    config = config_manager.get()
    
    bind_host = host or config.server.host
    bind_port = port or config.server.port
    
    console.print(Panel(
        f"[bold]Starting Sweave Web Server[/bold]\n"
        f"Host: [cyan]{bind_host}[/cyan]\n"
        f"Port: [cyan]{bind_port}[/cyan]\n"
        f"Reload: [cyan]{'enabled' if reload else 'disabled'}[/cyan]",
        title="Sweave Web",
        border_style="purple"
    ))
    
    import uvicorn
    uvicorn.run(
        "sweave.web.server:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
    )


@app.command()
def harness(
    list_: bool = typer.Option(False, "--list", "-l", help="List available harnesses"),
    detect: bool = typer.Option(False, "--detect", "-d", help="Auto-detect available harnesses"),
):
    """Manage and detect available harnesses."""
    if list_ or detect:
        _list_harnesses(detect)
    else:
        _list_harnesses(False)


def _list_harnesses(detect: bool):
    """List available harnesses."""
    import asyncio
    from sweave.harness import detect_all_harnesses, get_opencode_models
    
    console.print("[bold]Detecting harnesses...[/bold]")
    harnesses = asyncio.run(detect_all_harnesses())
    
    if not harnesses:
        console.print("[yellow]No harnesses detected[/yellow]")
        console.print("Install OpenCode, Claude Code, or Codex to get started")
        return
    
    table = Table(title="Available Harnesses")
    table.add_column("Name", style="cyan")
    table.add_column("Display Name", style="green")
    table.add_column("Command", style="yellow")
    table.add_column("Version", style="blue")
    table.add_column("Providers", style="magenta")
    
    for h in harnesses:
        table.add_row(
            h.name,
            h.display_name,
            h.command,
            h.version or "unknown",
            ", ".join(h.providers) if h.providers else "unknown",
        )
    
    console.print(table)
    
    # Show OpenCode models if available
    if any(h.name == "opencode" for h in harnesses):
        models = asyncio.run(get_opencode_models())
        if models:
            console.print("\n[bold]OpenCode Models by Provider:[/bold]")
            for provider, model_list in models.items():
                console.print(f"  [cyan]{provider}:[/cyan] {', '.join(model_list)}")


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# M1.9 step 4: visibility CLI commands (log / watch / tail).
# Imported at the bottom so the @app.command decorators register
# against the same ``app`` instance defined above. (Late import to
# avoid a circular dep with the panel module's helpers.)
# ---------------------------------------------------------------------------


@app.command()
def log(delegation_id: str = typer.Argument(..., help="Delegation id to render")):
    """Pretty-render a delegation's trace JSONL.

    Sections:
    * Composed prompt (memory/whats_new/synthesis/transcript_ref/user char counts).
    * Tool timeline (callID-keyed, snapshot state per tool).
    * Tokens / cost (aggregated across step-finish parts).
    * Status timeline (queued -> running -> review | done | failed).
    """
    from sweave.web.detail_view import render_detail_view

    trace_dir = Path.home() / ".sweave" / "traces"
    detail = render_detail_view(delegation_id, trace_dir=trace_dir)
    console.print(f"[bold]Delegation {detail['delegation_id']}[/bold]")
    if detail["composed_prompt"]:
        cp = detail["composed_prompt"]
        console.print(
            Panel(
                f"memory: {cp['memory_chars']}\n"
                f"whats_new: {cp['whats_new_chars']}\n"
                f"synthesis: {cp['synthesis_chars']}\n"
                f"transcript_ref: {cp['transcript_ref_chars']}\n"
                f"user: {cp['user_chars']}",
                title="Composed prompt",
                border_style="cyan",
            )
        )
    if detail["tool_timeline"]:
        table = Table(title="Tool timeline")
        table.add_column("callID", style="cyan")
        table.add_column("tool", style="green")
        table.add_column("status", style="yellow")
        table.add_column("title", style="blue")
        for t in detail["tool_timeline"]:
            table.add_row(
                t["callID"],
                t.get("tool") or "",
                t.get("status") or "",
                t.get("title") or "",
            )
        console.print(table)
    if detail["tokens"]:
        t = detail["tokens"]
        console.print(
            Panel(
                f"input: {t['input']}\n"
                f"output: {t['output']}\n"
                f"reasoning: {t['reasoning']}\n"
                f"cost: {t['cost']}",
                title="Tokens / cost",
                border_style="magenta",
            )
        )
    if detail["status_timeline"]:
        console.print("[bold]Status timeline:[/bold]")
        for c in detail["status_timeline"]:
            console.print(f"  - {c['status']}")
    if not any(
        [
            detail["composed_prompt"],
            detail["tool_timeline"],
            detail["tokens"],
            detail["status_timeline"],
        ]
    ):
        console.print(
            f"[yellow]No trace for delegation {delegation_id}.[/yellow]"
        )


@app.command()
def tail(delegation_id: str = typer.Argument(..., help="Delegation id to follow")):
    """Follow a running delegation's trace JSONL.

    Streams new lines as they're appended. Press Ctrl+C to stop.
    """
    import asyncio as _asyncio
    from sweave.cli.tail import follow_trace

    path = Path.home() / ".sweave" / "traces" / f"{delegation_id}.jsonl"
    if not path.exists():
        console.print(
            f"[yellow]Trace file not found for {delegation_id}; "
            f"waiting for it to appear at {path}.[/yellow]"
        )

    async def _run():
        async for line in follow_trace(path, from_start=False):
            console.print(line)

    try:
        _asyncio.run(_run())
    except KeyboardInterrupt:
        pass


@app.command()
def watch():
    """Watch the live delegation tree.

    Polls ``GET /api/delegations`` on the running server (default
    host/port from the MCP plumbing) and prints the tree of
    delegations + statuses. Updates every second. Press Ctrl+C to stop.
    """
    import asyncio as _asyncio

    async def _one_iteration() -> int:
        """Poll once. Returns the number of delegations seen."""
        import httpx as _httpx
        from sweave.mcp import _sweave_base_url

        base = _sweave_base_url()
        try:
            async with _httpx.AsyncClient(base_url=base, timeout=5.0) as client:
                r = await client.get("/api/delegations")
                if r.status_code >= 400:
                    console.print(
                        f"[red]Server returned {r.status_code} for /api/delegations[/red]"
                    )
                    return 0
                data = r.json()
                delegations = data.get("delegations") or []
                if not delegations:
                    console.print("[dim]No delegations yet.[/dim]")
                    return 0
                table = Table(title=f"Live tree ({len(delegations)} delegations)")
                table.add_column("id", style="cyan")
                table.add_column("agent", style="green")
                table.add_column("status", style="yellow")
                table.add_column("kind", style="magenta")
                for d in delegations[:20]:
                    table.add_row(
                        d.get("delegation_id", "?"),
                        d.get("agent", "?"),
                        d.get("status", "?"),
                        d.get("kind", "?"),
                    )
                console.print(table)
                return len(delegations)
        except _httpx.ConnectError:
            console.print(
                f"[yellow]No sweave server at {base}. Start the server with "
                f"`python start_server.py` first.[/yellow]"
            )
            return 0

    async def _loop():
        while True:
            await _one_iteration()
            await _asyncio.sleep(1.0)

    try:
        _asyncio.run(_loop())
    except KeyboardInterrupt:
        pass
#!/usr/bin/env python3
"""Initialize Hindsight memory backend"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
import typer
from rich.console import Console

console = Console()
app = typer.Typer()


@app.command()
def init(
    mode: str = typer.Option("embedded_slim", "--mode", "-m", help="Hindsight mode: embedded_slim, docker_full, docker_slim, cloud"),
    api_key: str = typer.Option(None, "--api-key", help="Hindsight Cloud API key"),
    openai_key: str = typer.Option(None, "--openai-key", help="OpenAI API key for embeddings"),
    embeddings_provider: str = typer.Option("openai", "--embeddings", help="Embeddings provider: openai, cohere, tei, local"),
    reranker_provider: str = typer.Option("openai", "--reranker", help="Reranker provider: openai, cohere, tei, local"),
):
    """Initialize Hindsight memory backend."""
    
    if mode == "cloud":
        if not api_key:
            api_key = typer.prompt("Hindsight Cloud API key")
        _init_cloud(api_key)
    elif mode.startswith("docker"):
        _init_docker(mode, openai_key, embeddings_provider, reranker_provider)
    else:
        _init_embedded(openai_key, embeddings_provider, reranker_provider)


def _init_cloud(api_key: str):
    """Initialize Hindsight Cloud."""
    console.print("[green]Hindsight Cloud configured[/green]")
    console.print("Set HINDSIGHT_API_KEY in your environment or config.yaml")


def _init_docker(mode: str, openai_key: str, embeddings_provider: str, reranker_provider: str):
    """Initialize Hindsight via Docker."""
    image = "ghcr.io/vectorize-io/hindsight:latest" if mode == "docker_full" else "ghcr.io/vectorize-io/hindsight:slim"
    
    # Check Docker
    try:
        subprocess.run(["docker", "--version"], capture_output=True, check=True)
    except Exception:
        console.print("[red]Docker not found. Please install Docker first.[/red]")
        sys.exit(1)
    
    # Build docker run command
    env = {}
    if openai_key:
        env["HINDSIGHT_API_LLM_API_KEY"] = openai_key
        env["HINDSIGHT_API_EMBEDDINGS_API_KEY"] = openai_key
        env["HINDSIGHT_API_RERANKER_API_KEY"] = openai_key
    elif os.environ.get("OPENAI_API_KEY"):
        env["HINDSIGHT_API_LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]
        env["HINDSIGHT_API_EMBEDDINGS_API_KEY"] = os.environ["OPENAI_API_KEY"]
        env["HINDSIGHT_API_RERANKER_API_KEY"] = os.environ["OPENAI_API_KEY"]
    
    env["HINDSIGHT_API_EMBEDDINGS_PROVIDER"] = embeddings_provider
    env["HINDSIGHT_API_RERANKER_PROVIDER"] = reranker_provider
    
    cmd = [
        "docker", "run", "-d",
        "--name", "sweave-hindsight",
        "-p", "8888:8888",
        "-p", "9999:9999",
        "-v", "sweave-hindsight-data:/home/hindsight/.pg0",
    ]
    
    for k, v in env.items():
        cmd.extend(["-e", f"{k}={v}"])
    
    cmd.append(image)
    
    console.print(f"Starting Hindsight {mode} container...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            console.print(f"[green]Hindsight started: {result.stdout.strip()[:12]}[/green]")
            console.print("API available at http://localhost:8888")
        else:
            console.print(f"[red]Failed to start:[/red] {result.stderr}")
    except subprocess.TimeoutExpired:
        console.print("[red]Docker start timed out[/red]")


def _init_embedded(openai_key: str, embeddings_provider: str, reranker_provider: str):
    """Initialize embedded Hindsight."""
    console.print("[green]Embedded Hindsight (hindsight-all-slim) selected[/green]")
    console.print("Ensure you have installed: pip install hindsight-all-slim")
    
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
    elif not os.environ.get("OPENAI_API_KEY"):
        console.print("[yellow]Warning: No OpenAI API key set. Embeddings will fail without it.[/yellow]")
        console.print("Set OPENAI_API_KEY or use --openai-key")


@app.command()
def stop():
    """Stop Hindsight Docker container."""
    try:
        subprocess.run(["docker", "stop", "sweave-hindsight"], capture_output=True)
        console.print("[green]Hindsight container stopped[/green]")
    except Exception:
        console.print("[yellow]No container to stop[/yellow]")


@app.command()
def status():
    """Check Hindsight status."""
    # Check Docker
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=sweave-hindsight", "--format", "{{.Status}}"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            console.print(f"[green]Docker container running:[/green] {result.stdout.strip()}")
        else:
            console.print("[yellow]No Docker container running[/yellow]")
    except Exception:
        console.print("[red]Docker not available[/red]")
    
    # Check embedded
    try:
        import hindsight_client
        console.print("[green]hindsight-client installed[/green]")
    except ImportError:
        console.print("[yellow]hindsight-client not installed[/yellow]")


if __name__ == "__main__":
    app()
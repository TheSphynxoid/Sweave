"""Missing-provider mapping tests (user-reported auth_missing).

`zai-coding-plan` (Z.AI coding subscription, separate /coding/
endpoint per models.dev) and `thinkingmachines` (first-party
Anthropic-native tinker API) had no sidecar TABLE entry, so every
engine turn died at resolve with `auth_missing ... no
OpenAI-compatible surface mapped` while the opencode harness served
the same models fine.

Covers:
* resolution (node-level, hermetic): endpoint + flavor + header
  mode for both providers; unknown providers and the google flavor
  keep their loud failures;
* end-to-end: a zai-coding-plan chat turn serves through the mapped
  endpoint (the exact reported failure shape).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.sidecars import (
    read_port_line,
    spawn_sidecar,
    stop_sidecar,
    wait_for_health,
)

node_missing = shutil.which("node") is None
needs_node = pytest.mark.skipif(node_missing, reason="node not on PATH")


def _node_resolve(env: dict, provider: str, model: str) -> dict:
    """Run resolveProvider() in-process-equivalent isolation (fake
    HOME so the opencode auth-store bootstrap cannot interfere)."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        "import('./sweave-engine/src/providers.js').then((m) => {"
        f"console.log(JSON.stringify(m.resolveProvider({json.dumps(provider)}, {json.dumps(model)})));"
        "});"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _isolated_env(tmp_path_factory, **extra: str) -> dict:
    home = tmp_path_factory.mktemp("prov-fake-home")
    return {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        **extra,
    }


def test_zai_coding_plan_resolves_chat(tmp_path_factory):
    env = _isolated_env(
        tmp_path_factory,
        SWEAVE_ENGINE_BASE_ZAI_CODING_PLAN="http://127.0.0.1:9/",
        SWEAVE_ENGINE_KEY_ZAI_CODING_PLAN="test-coding-key",
    )
    out = _node_resolve(env, "zai-coding-plan", "glm-5.3-flash")
    assert out["ok"] is True
    assert out["baseURL"] == "http://127.0.0.1:9/"
    assert out["flavor"] == "chat"
    assert not out.get("anthropicAuth")


def test_zai_coding_plan_default_endpoint_without_override(tmp_path_factory):
    env = _isolated_env(
        tmp_path_factory,
        ZHIPU_API_KEY="test-zhipu-key",
    )
    out = _node_resolve(env, "zai-coding-plan", "glm-5.3-flash")
    assert out["ok"] is True
    assert out["baseURL"] == "https://api.z.ai/api/coding/paas/v4"
    assert out["via"] == "env:ZHIPU_API_KEY"


def test_thinkingmachines_resolves_messages_anthropic_auth(tmp_path_factory):
    env = _isolated_env(
        tmp_path_factory,
        SWEAVE_ENGINE_BASE_THINKINGMACHINES="http://127.0.0.1:9/",
        SWEAVE_ENGINE_KEY_THINKINGMACHINES="test-tinker-key",
    )
    out = _node_resolve(env, "thinkingmachines", "Inkling")
    assert out["ok"] is True
    assert out["flavor"] == "messages"
    assert out.get("anthropicAuth") is True


def test_thinkingmachines_unknown_id_defaults_messages(tmp_path_factory):
    env = _isolated_env(
        tmp_path_factory,
        SWEAVE_ENGINE_KEY_THINKINGMACHINES="test-tinker-key",
    )
    out = _node_resolve(env, "thinkingmachines", "Future-Model-X")
    assert out["ok"] is True
    assert out["flavor"] == "messages"


def test_unknown_provider_still_auth_missing(tmp_path_factory):
    env = _isolated_env(tmp_path_factory)
    out = _node_resolve(env, "nope-provider", "m")
    assert out["ok"] is False
    assert out["code"] == "auth_missing"


def test_google_flavor_still_pending(tmp_path_factory):
    env = _isolated_env(
        tmp_path_factory,
        SWEAVE_ENGINE_BASE_OPENCODE="http://127.0.0.1:9/",
        SWEAVE_ENGINE_KEY_OPENCODE="k",
    )
    out = _node_resolve(env, "opencode", "gemini-3-flash")
    assert out["ok"] is False
    assert out["code"] == "bad_request"
    assert "google" in out["reason"]


# ---------------------------------------------------------------------------
# End-to-end: the reported failure shape serves now
# ---------------------------------------------------------------------------


class _ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):
        if self.path == "/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                body = {}
            assert body.get("model") == "glm-5.3-flash", body.get("model")
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(
                    f"data: {json.dumps({'choices': [{'delta': {'content': 'CODING OK'}}]})}\n\n".encode()
                )
                self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            return
        payload = json.dumps({"error": "stub"}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture(scope="module")
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def sidecar(stub_url, tmp_path_factory):
    if node_missing:
        pytest.skip("node not on PATH")
    home = tmp_path_factory.mktemp("prov-e2e-home")
    data_dir = tmp_path_factory.mktemp("prov-e2e-data")
    repo_root = Path(__file__).resolve().parents[1]
    proc = spawn_sidecar(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0", "--data-dir", str(data_dir)],
        env={
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "SWEAVE_ENGINE_BASE_ZAI_CODING_PLAN": stub_url,
            "SWEAVE_ENGINE_KEY_ZAI_CODING_PLAN": "test-coding-key",
            "SWEAVE_API_URL": stub_url,
            "SWEAVE_MCP_TOKEN": "stub-token",
        },
    )
    line = read_port_line(proc)
    assert line.startswith("SWEAVE_ENGINE_PORT="), line
    url = f"http://127.0.0.1:{line.split('=', 1)[1]}"
    wait_for_health(url)
    saved = os.environ.get("SWEAVE_ENGINE_URL")
    os.environ["SWEAVE_ENGINE_URL"] = url
    try:
        yield url
    finally:
        if saved is None:
            os.environ.pop("SWEAVE_ENGINE_URL", None)
        else:
            os.environ["SWEAVE_ENGINE_URL"] = saved
        stop_sidecar(proc)


@needs_node
async def test_zai_coding_plan_turn_serves(sidecar, tmp_path):
    """The reported auth_missing shape is now a working chat turn."""
    from sweave.harness.base import AgentSpec, Message
    from sweave.harness.engine import SweaveEngineHarness

    spec = AgentSpec(
        name="worker",
        role="specialist",
        model="zai-coding-plan/glm-5.3-flash",
        system_prompt="",
        worktree_path=tmp_path,
        memory_bank="session-prov",
        tools=[],
        harness="sweave-engine",
    )
    proc = await SweaveEngineHarness().spawn(spec)
    result = await proc.send(Message(type="user", content="hi", metadata={}))
    assert result.success, result.error
    assert result.output == "CODING OK"

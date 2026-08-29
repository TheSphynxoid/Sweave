#!/usr/bin/env python3
"""
Sweave - One-shot runner that starts the web server and keeps it running.
Works on both Linux and Windows.
"""

import argparse
import subprocess
import sys
import time
import signal
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Sweave Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--check", action="store_true", help="Run test suite and exit")
    args = parser.parse_args()

    # Change to project root
    project_root = Path(__file__).parent.resolve()
    os.chdir(project_root)

    if args.check:
        # Run test suite
        return run_tests(args)
    else:
        # Run server (foreground)
        cmd = [sys.executable, "-m", "sweave.cli.main", "web",
               "--host", args.host, "--port", str(args.port)]
        if args.reload:
            cmd.append("--reload")

        print(f"Starting Sweave Web Server on http://{args.host}:{args.port}")
        print(f"Press Ctrl+C to stop")
        print()

        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nShutting down...")
        return 0


def run_tests(args):
    """Run the test suite."""
    print("=" * 60)
    print("SWEAVE WEB SERVER TEST SUITE")
    print("=" * 60)

    # Start server in background
    log = open("web.log", "w")
    err = open("web_err.log", "w")
    process = subprocess.Popen(
        [sys.executable, "-m", "sweave.cli.main", "web",
         "--host", "127.0.0.1", "--port", "9091"],
        stdout=log, stderr=err,
        cwd=Path.cwd()
    )

    import requests

    base_url = "http://127.0.0.1:9091"
    print(f"Starting server...")

    for i in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{base_url}/api/agents", timeout=2)
            if r.status_code == 200:
                print(f"Server ready")
                break
        except:
            pass
    else:
        print("Server failed to start within 30s")
        process.kill()
        return 1

    # Run tests
    tests = [
        ("GET / (SPA)", "GET", "/", None, lambda r: "Sweave" in r.text),
        ("GET /static/style.css", "GET", "/static/style.css", None, lambda r: r.status_code == 200),
        ("GET /static/js/app.js", "GET", "/static/js/app.js", None, lambda r: "switchTab" in r.text and "setupEventListeners" in r.text),
        ("GET /api/agents", "GET", "/api/agents", None, lambda r: r.status_code == 200),
        ("GET /api/models", "GET", "/api/models", None, lambda r: r.status_code == 200),
        ("GET /api/rules", "GET", "/api/rules", None, lambda r: r.status_code == 200),
        ("GET /api/projects", "GET", "/api/projects", None, lambda r: r.status_code == 200),
        ("GET /api/sessions", "GET", "/api/sessions", None, lambda r: r.status_code == 200),
        ("GET /api/memory/banks", "GET", "/api/memory/banks", None, lambda r: r.status_code == 200),
        ("GET /api/fs/drives", "GET", "/api/fs/drives", None, lambda r: r.status_code == 200),
        ("GET /api/config", "GET", "/api/config", None, lambda r: r.status_code == 200),
        ("POST /api/route", "POST", "/api/route", {"task": "Build a REST API"}, lambda r: r.status_code == 200 and "agent" in r.json()),
        ("POST /api/agents (create)", "POST", "/api/agents", {
            "name": "test-agent-x",
            "role": "tester",
            "model": "deepseek-coder",
            "system_prompt": "Test",
            "harness": "opencode"
        }, lambda r: r.status_code == 200),
    ]

    passed = 0
    failed = 0

    for name, method, path, data, check in tests:
        try:
            if method == "GET":
                r = requests.get(f"{base_url}{path}", timeout=5)
            else:
                r = requests.post(f"{base_url}{path}", json=data, timeout=5)

            if check(r):
                print(f"  [PASS] {name}")
                passed += 1
            else:
                print(f"  [FAIL] {name}")
                failed += 1
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            failed += 1

    # Cleanup test agent
    try:
        requests.delete(f"{base_url}/api/agents/test-agent-x", timeout=5)
    except:
        pass

    print()
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    # Stop server
    process.terminate()
    try:
        process.wait(timeout=5)
    except:
        process.kill()
    log.close()
    err.close()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
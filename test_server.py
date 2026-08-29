#!/usr/bin/env python3
"""Test script to verify the Sweave web server is working properly."""

import requests
import json
import time
import sys
import subprocess
import signal
import os
from pathlib import Path

def start_server(port=9091, host="127.0.0.1"):
    """Start the Sweave web server in a background process."""
    print(f"Starting Sweave web server on {host}:{port}...")

    # Start server in background
    log_file = open("web.log", "w")
    err_file = open("web_err.log", "w")

    process = subprocess.Popen(
        ["python", "-m", "sweave.cli.main", "web", "--host", host, "--port", str(port)],
        stdout=log_file,
        stderr=err_file,
        cwd=Path.cwd()
    )

    # Wait for server to be ready
    base_url = f"http://{host}:{port}"
    for i in range(30):
        time.sleep(1)
        try:
            response = requests.get(f"{base_url}/api/agents", timeout=2)
            if response.status_code == 200:
                print(f"Server ready at {base_url}")
                return process, base_url
        except:
            pass
        if i % 5 == 0:
            print(f"  Waiting... ({i+1}s)")

    print("Server failed to start within 30 seconds")
    process.kill()
    return None, None


def test_endpoints(base_url):
    """Test all major API endpoints."""
    print("\n" + "="*60)
    print("TESTING SWEAVE WEB SERVER ENDPOINTS")
    print("="*60)

    tests_passed = 0
    tests_failed = 0

    # Test 1: Index HTML
    print("\n[1] Testing GET / (index.html)...")
    try:
        response = requests.get(f"{base_url}/", timeout=5)
        if response.status_code == 200 and "Sweave" in response.text:
            print(f"  [PASS] PASS: Index HTML loaded ({len(response.text)} bytes)")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 2: Agents endpoint
    print("\n[2] Testing GET /api/agents...")
    try:
        response = requests.get(f"{base_url}/api/agents", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Got {len(data.get('builtin', {}))} built-in agents, {len(data.get('dynamic', {}))} dynamic")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 3: Models endpoint
    print("\n[3] Testing GET /api/models...")
    try:
        response = requests.get(f"{base_url}/api/models", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Got {len(data.get('roles', {}))} model roles")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 4: Rules endpoint
    print("\n[4] Testing GET /api/rules...")
    try:
        response = requests.get(f"{base_url}/api/rules", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Got {len(data.get('routes', []))} routing rules")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 5: Worktrees endpoint
    print("\n[5] Testing GET /api/worktrees...")
    try:
        response = requests.get(f"{base_url}/api/worktrees", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Got {len(data.get('worktrees', []))} worktrees")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 6: Config endpoint
    print("\n[6] Testing GET /api/config...")
    try:
        response = requests.get(f"{base_url}/api/config", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Config loaded (server: {data.get('server', {}).get('host')}:{data.get('server', {}).get('port')})")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 7: Static files
    print("\n[7] Testing GET /static/style.css...")
    try:
        response = requests.get(f"{base_url}/static/style.css", timeout=5)
        if response.status_code == 200 and "Sweave" in response.text:
            print(f"  [PASS] PASS: CSS loaded ({len(response.text)} bytes)")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 8: JavaScript modules
    print("\n[8] Testing GET /static/js/app.js...")
    try:
        response = requests.get(f"{base_url}/static/js/app.js", timeout=5)
        if response.status_code == 200 and "import" in response.text:
            print(f"  [PASS] PASS: app.js loaded ({len(response.text)} bytes)")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 9: API client
    print("\n[9] Testing GET /static/js/api.js...")
    try:
        response = requests.get(f"{base_url}/static/js/api.js", timeout=5)
        if response.status_code == 200 and "API" in response.text:
            print(f"  [PASS] PASS: api.js loaded ({len(response.text)} bytes)")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 10: Route task
    print("\n[10] Testing POST /api/route...")
    try:
        response = requests.post(
            f"{base_url}/api/route",
            json={"task": "Build a REST API with JWT authentication"},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            print(f"  [PASS] PASS: Route to '{data.get('agent')}' with model '{data.get('model')}'")
            tests_passed += 1
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 11: Create agent
    print("\n[11] Testing POST /api/agents (create)...")
    try:
        test_agent = {
            "name": "test-agent-001",
            "role": "tester",
            "model": "deepseek-coder",
            "system_prompt": "You are a test agent for verifying the API works correctly.",
            "description": "Test agent for API verification",
            "tools": ["hindsight_recall", "hindsight_retain"],
            "harness": "opencode"
        }
        response = requests.post(
            f"{base_url}/api/agents",
            json=test_agent,
            timeout=5
        )
        if response.status_code == 200:
            print(f"  [PASS] PASS: Agent created successfully")
            tests_passed += 1

            # Clean up
            requests.delete(f"{base_url}/api/agents/test-agent-001", timeout=5)
        else:
            print(f"  [FAIL] FAIL: Status {response.status_code}")
            tests_failed += 1
    except Exception as e:
        print(f"  [FAIL] FAIL: {e}")
        tests_failed += 1

    # Test 12: Page routes (SPA)
    print("\n[12] Testing SPA routes (/agents, /tasks, etc.)...")
    routes = ["/agents", "/tasks", "/worktrees", "/memory", "/settings"]
    for route in routes:
        try:
            response = requests.get(f"{base_url}{route}", timeout=5)
            if response.status_code == 200 and "Sweave" in response.text:
                print(f"  [PASS] PASS: {route} returns SPA")
                tests_passed += 1
            else:
                print(f"  [FAIL] FAIL: {route} returned {response.status_code}")
                tests_failed += 1
        except Exception as e:
            print(f"  [FAIL] FAIL: {route} - {e}")
            tests_failed += 1

    # Summary
    print("\n" + "="*60)
    print(f"RESULTS: {tests_passed} passed, {tests_failed} failed")
    print("="*60)

    return tests_passed, tests_failed


def main():
    """Main test function."""
    print("="*60)
    print("SWEAVE WEB SERVER TEST SUITE")
    print("="*60)

    # Start server
    process, base_url = start_server(port=9091)

    if not process or not base_url:
        print("\nFailed to start server. Exiting.")
        return 1

    try:
        # Run tests
        passed, failed = test_endpoints(base_url)

        if failed == 0:
            print("\n[PASS] All tests passed!")
            return 0
        else:
            print(f"\n[FAIL] {failed} tests failed")
            return 1
    finally:
        # Cleanup
        print("\nStopping server...")
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            process.kill()
        print("Server stopped")


if __name__ == "__main__":
    sys.exit(main())

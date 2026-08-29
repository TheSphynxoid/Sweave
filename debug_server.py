#!/usr/bin/env python3
"""Start server and check for client-side errors by inspecting the page."""

import subprocess
import time
import requests
import sys
from pathlib import Path

def start_server(port=9094):
    """Start the Sweave web server."""
    print(f"Starting server on port {port}...")

    log = open("web_debug.log", "w")
    err = open("web_debug_err.log", "w")

    process = subprocess.Popen(
        ["python", "-m", "sweave.cli.main", "web", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log, stderr=err,
        cwd=Path.cwd()
    )

    base_url = f"http://127.0.0.1:{port}"
    for i in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{base_url}/api/agents", timeout=2)
            if r.status_code == 200:
                print(f"Server ready at {base_url}")
                return process, base_url, log, err
        except:
            pass

    print("Server failed to start")
    process.kill()
    return None, None, log, err


def check_page_content(base_url):
    """Check that the page contains the elements needed to render."""
    print("\nChecking page content...")

    response = requests.get(f"{base_url}/", timeout=5)
    html = response.text

    # Check for key elements
    checks = [
        ('id="app"', "App container element"),
        ('id="app-loader"', "Loader element"),
        ('class="hidden"', "App starts hidden (should be revealed by JS)"),
        ('/static/js/app.js', "Main JS file"),
        ('/static/style.css', "CSS file"),
    ]

    for needle, desc in checks:
        if needle in html:
            print(f"  [OK] {desc}")
        else:
            print(f"  [MISSING] {desc}")

    # Check that the CSS doesn't reference missing files that could break layout
    print("\nChecking for resource issues...")
    css_response = requests.get(f"{base_url}/static/style.css")
    css = css_response.text

    if "@font-face" in css:
        print("  [INFO] CSS uses @font-face (will fall back if files missing)")

    if "url('/static/fonts/" in css:
        print("  [WARN] CSS references /static/fonts/ - these will 404 but shouldn't break the page")

    return True


def test_with_requests(base_url):
    """Test the API endpoints work."""
    print("\nTesting API endpoints...")

    endpoints = [
        ("/api/agents", "GET"),
        ("/api/models", "GET"),
        ("/api/rules", "GET"),
        ("/api/worktrees", "GET"),
        ("/api/config", "GET"),
    ]

    for path, method in endpoints:
        try:
            r = requests.get(f"{base_url}{path}", timeout=5)
            if r.status_code == 200:
                data = r.json()
                print(f"  [OK] {method} {path} -> {r.status_code}")
            else:
                print(f"  [FAIL] {method} {path} -> {r.status_code}")
        except Exception as e:
            print(f"  [ERROR] {method} {path} -> {e}")


def main():
    process, base_url, log, err = start_server()

    if not process:
        sys.exit(1)

    try:
        check_page_content(base_url)
        test_with_requests(base_url)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except:
            process.kill()
        log.close()
        err.close()


if __name__ == "__main__":
    main()
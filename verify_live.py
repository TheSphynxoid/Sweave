#!/usr/bin/env python3
"""Verify the running server is serving the correct content."""

import requests
import sys

PORT = 8090
BASE = f"http://127.0.0.1:{PORT}"

def check(name, url, expected_in_text=None, expected_status=200):
    try:
        r = requests.get(url, timeout=3)
        status_ok = r.status_code == expected_status
        text_ok = expected_in_text is None or expected_in_text in r.text
        if status_ok and text_ok:
            print(f"  [PASS] {name} -> {r.status_code}, {len(r.text)} bytes")
            return True
        else:
            print(f"  [FAIL] {name} -> {r.status_code}, status_ok={status_ok}, text_ok={text_ok}")
            return False
    except Exception as e:
        print(f"  [ERROR] {name}: {e}")
        return False

def main():
    print(f"Verifying server at {BASE}")
    print()

    all_pass = True

    # Test main page
    if not check("GET /", f"{BASE}/", "Sweave"):
        all_pass = False

    # Test static assets
    if not check("GET /static/style.css", f"{BASE}/static/style.css"):
        all_pass = False

    if not check("GET /static/js/app.js", f"{BASE}/static/js/app.js"):
        all_pass = False

    # Test fonts (should 404, but page should still work)
    try:
        r = requests.get(f"{BASE}/static/fonts/FiraCode-Regular.woff2", timeout=3)
        if r.status_code == 404:
            print(f"  [PASS] GET /static/fonts/FiraCode-Regular.woff2 -> 404 (expected, page uses system fonts)")
        else:
            print(f"  [WARN] Font returned {r.status_code}, expected 404")
    except Exception as e:
        print(f"  [WARN] Font check error: {e}")

    # Test API endpoints
    if not check("GET /api/agents", f"{BASE}/api/agents"):
        all_pass = False

    if not check("GET /api/models", f"{BASE}/api/models"):
        all_pass = False

    if not check("GET /api/rules", f"{BASE}/api/rules"):
        all_pass = False

    if not check("GET /api/config", f"{BASE}/api/config"):
        all_pass = False

    # Test route preview
    try:
        r = requests.post(f"{BASE}/api/route", json={"task": "Build a REST API"}, timeout=3)
        if r.status_code == 200 and "agent" in r.json():
            data = r.json()
            print(f"  [PASS] POST /api/route -> {data.get('agent')} ({data.get('model')})")
        else:
            print(f"  [FAIL] POST /api/route -> {r.status_code}")
            all_pass = False
    except Exception as e:
        print(f"  [ERROR] /api/route: {e}")
        all_pass = False

    # Test SPA routes
    for route in ["/agents", "/tasks", "/worktrees", "/memory", "/settings"]:
        if not check(f"GET {route}", f"{BASE}{route}", "Sweave"):
            all_pass = False

    print()
    if all_pass:
        print("[OK] All checks passed!")
        print()
        print(f"Server is running at: {BASE}")
        print("Open this URL in your browser to see the UI")
        return 0
    else:
        print("[FAIL] Some checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
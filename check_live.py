#!/usr/bin/env python3
"""Quick check that the server is running and the page loads."""

import requests
import sys

try:
    # Check if server is running on port 8082
    r = requests.get("http://127.0.0.1:8082/", timeout=5)
    print(f"GET / -> {r.status_code}, {len(r.text)} bytes")

    if "Sweave" in r.text and "app-loader" in r.text:
        print("[OK] Index HTML contains expected elements")
    else:
        print("[WARN] Index HTML missing expected elements")
        print("First 500 chars:", r.text[:500])
        sys.exit(1)

    # Check fonts don't 404
    r = requests.get("http://127.0.0.1:8082/static/fonts/FiraCode-Regular.woff2", timeout=5)
    print(f"GET /static/fonts/FiraCode-Regular.woff2 -> {r.status_code}")
    if r.status_code == 404:
        print("[OK] Font 404 expected (system fonts will be used)")

    # Check API
    r = requests.get("http://127.0.0.1:8082/api/agents", timeout=5)
    print(f"GET /api/agents -> {r.status_code}")
    data = r.json()
    print(f"  Built-in: {list(data.get('builtin', {}).keys())}")
    print(f"  Dynamic: {list(data.get('dynamic', {}).keys())}")

    print("\n[OK] Server is running and serving correctly!")
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
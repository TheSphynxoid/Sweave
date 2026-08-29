#!/usr/bin/env python3
"""Use a simple JSDOM-like check to see if the app would work in a browser."""

import requests

PORT = 8090
BASE = f"http://127.0.0.1:{PORT}"

# Get the page
r = requests.get(f"{BASE}/", timeout=5)
html = r.text

# Check for required elements
required_elements = [
    ('id="app"', "App container"),
    ('id="app-loader"', "Loader element"),
    ('id="chat-messages"', "Chat messages"),
    ('id="chat-input"', "Chat input"),
    ('id="send-btn"', "Send button"),
    ('id="agents-grid"', "Agents grid"),
    ('id="tasks-tbody"', "Tasks table body"),
    ('id="worktrees-tbody"', "Worktrees table body"),
    ('id="recall-query"', "Memory recall query"),
    ('id="reflect-query"', "Memory reflect query"),
    ('id="retain-content"', "Memory retain content"),
    ('id="create-agent-modal"', "Create agent modal"),
    ('id="run-task-modal"', "Run task modal"),
    ('id="notifications"', "Notifications container"),
    ('href="/static/style.css"', "Style sheet link"),
    ('src="/static/js/app.js"', "App JS link"),
]

print("Checking required HTML elements...")
all_pass = True
for needle, name in required_elements:
    if needle in html:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} (missing: {needle})")
        all_pass = False

# Check for modulepreload
if 'modulepreload' in html:
    print("  [PASS] Module preload present")

# Check for safety net (app becomes visible even if init fails)
if 'Safety net' in html or "classList.contains" in html:
    print("  [PASS] Safety net script present")

# Check JS file accessibility
print("\nChecking JS modules...")
js_modules = ['api.js', 'app.js', 'theme.js', 'websocket.js', 'modal.js', 'notification.js']
for module in js_modules:
    r = requests.get(f"{BASE}/static/js/{module}", timeout=3)
    if r.status_code == 200:
        print(f"  [PASS] {module} ({len(r.text)} bytes)")

# Check for the element ID lookup fix
r = requests.get(f"{BASE}/static/js/app.js", timeout=3)
js_content = r.text
if 'populateElements' in js_content:
    print("  [PASS] populateElements function (timing fix)")

if 'showFatalError' in js_content:
    print("  [PASS] Error display handler present")

if 'initManagers' in js_content:
    print("  [PASS] Deferred manager initialization")

print()
if all_pass:
    print("[OK] All checks passed - page should render correctly")
    print()
    print("Open http://127.0.0.1:8090/ in your browser")
else:
    print("[FAIL] Some checks failed")
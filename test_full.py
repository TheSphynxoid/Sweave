"""Comprehensive end-to-end test."""
import requests
import sys

PORT = 8100
BASE = f"http://127.0.0.1:{PORT}"

def check(name, ok, details=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" - {details}" if details else ""))
    return ok

print(f"=== Final Sweave Test at {BASE} ===\n")

passed = 0
failed = 0

# 1. Page loads
r = requests.get(f"{BASE}/", timeout=5)
if check("GET /", r.status_code == 200, f"{len(r.text)} bytes"):
    passed += 1
else:
    failed += 1

html = r.text

# 2. App div starts hidden
if check("app div starts hidden", 'id="app" class="app hidden"' in html):
    passed += 1
else:
    failed += 1

# 3. All key elements present
elements = [
    "loader", "app", "welcome", "sidebar", "project-link",
    "session-link", "new-session-btn", "sidebar-toggle",
    "tab-chat", "tab-children", "tab-agents", "tab-memory", "tab-settings",
    "project-modal", "session-modal", "fb-drives", "memory-bank",
    "error-box",
]
for el in elements:
    if check(f"Element #{el}", f'id="{el}"' in html):
        passed += 1
    else:
        failed += 1

# 4. All 5 nav items
for tab in ["chat", "children", "agents", "memory", "settings"]:
    if check(f"Nav item: {tab}", f'data-tab="{tab}"' in html):
        passed += 1
    else:
        failed += 1

# 5. CSS
r = requests.get(f"{BASE}/static/style.css", timeout=5)
if check("CSS loads", r.status_code == 200):
    passed += 1
else:
    failed += 1

css = r.text
if check("CSS fullscreen", "100vh" in css and "100vw" in css):
    passed += 1
else:
    failed += 1

# 6. JS - the critical fix
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
if check("JS loads", r.status_code == 200):
    passed += 1
else:
    failed += 1

js = r.text
# Check for the visibility fix
if "app.classList.remove('hidden')" in js:
    if check("App visibility fix present", True):
        passed += 1
    else:
        failed += 1
else:
    if check("App visibility fix MISSING", False):
        failed += 1
    else:
        passed += 1

# Check for error handlers
if "window.addEventListener('error'" in js:
    if check("Global error handler", True):
        passed += 1
    else:
        failed += 1
else:
    if check("Global error handler MISSING", False):
        failed += 1
    else:
        passed += 1

# Check for nav handler
if "switchTab" in js and "nav-item" in js and "addEventListener" in js:
    if check("Nav navigation code present", True):
        passed += 1
    else:
        failed += 1
else:
    if check("Nav navigation MISSING", False):
        failed += 1
    else:
        passed += 1

# 7. All API endpoints
print("\n=== API Endpoints ===")
endpoints = [
    ("/projects", "List projects"),
    ("/sessions", "List sessions"),
    ("/memory/banks", "Memory banks"),
    ("/agents", "List agents"),
    ("/models", "Models"),
    ("/rules", "Routing rules"),
    ("/config", "Config"),
    ("/fs/drives", "File system drives"),
]
for path, name in endpoints:
    r = requests.get(f"{BASE}/api{path}", timeout=5)
    if check(f"GET /api{path}", r.status_code == 200):
        passed += 1
    else:
        failed += 1

# 8. File browser
print("\n=== File Browser ===")
r = requests.get(f"{BASE}/api/fs/list", params={"path": "C:\\"}, timeout=5)
if check("List C:\\", r.status_code == 200):
    passed += 1
    data = r.json()
    print(f"    {data.get('total')} entries")
else:
    failed += 1

print(f"\n=== Final Results: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
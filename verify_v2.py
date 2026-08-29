import requests
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8092
BASE = f"http://127.0.0.1:{PORT}"

def check(name, ok, details=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" - {details}" if details else ""))
    return ok

print(f"=== Verifying Sweave at {BASE} ===\n")

passed = 0
failed = 0

# Test 1: Page loads
r = requests.get(f"{BASE}/", timeout=5)
if check("GET / returns HTML", r.status_code == 200, f"{len(r.text)} bytes"):
    passed += 1
else:
    failed += 1

html = r.text
if check("Has #app container", 'id="app"' in html):
    passed += 1
else:
    failed += 1
if check("Has #loader", 'id="loader"' in html):
    passed += 1
else:
    failed += 1
if check("Has project-welcome screen", 'id="project-welcome"' in html):
    passed += 1
else:
    failed += 1
if check("Has main-panel", 'id="main-panel"' in html):
    passed += 1
else:
    failed += 1
if check("Has all 5 nav tabs", all(f'data-tab="{t}"' in html for t in ['chat', 'children', 'agents', 'memory', 'settings'])):
    passed += 1
else:
    failed += 1
if check("Has welcome action cards", 'action-open-folder-btn' in html and 'action-create-project-btn' in html):
    passed += 1
else:
    failed += 1

# Test 2: CSS loads
r = requests.get(f"{BASE}/static/style.css", timeout=5)
if check("CSS loads", r.status_code == 200, f"{len(r.text)} bytes"):
    passed += 1
else:
    failed += 1
css = r.text
if check("CSS has fullscreen layout", "height: 100vh" in css and "width: 100vw" in css):
    passed += 1
else:
    failed += 1
if check("CSS has all components", all(c in css for c in ['.topbar', '.sidebar', '.content-area', '.tab-panel', '.modal'])):
    passed += 1
else:
    failed += 1

# Test 3: JS loads
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
if check("JS loads", r.status_code == 200, f"{len(r.text)} bytes"):
    passed += 1
else:
    failed += 1
js = r.text
if check("JS has init function", "async function init" in js):
    passed += 1
else:
    failed += 1
if check("JS has all tab functions", all(f in js for f in ['switchTab', 'sendChat', 'createNewSession', 'switchMemoryTab', 'addRule', 'saveAgent'])):
    passed += 1
else:
    failed += 1
if check("JS has API client", "listProjects" in js and "createProject" in js):
    passed += 1
else:
    failed += 1
if check("JS has all event listeners", "setupEventListeners" in js and "DOMContentLoaded" in js):
    passed += 1
else:
    failed += 1
if check("JS has theme system", "applyTheme" in js and "THEMES" in js):
    passed += 1
else:
    failed += 1
if check("JS has file browser", "loadFBPath" in js and "loadFBDrives" in js):
    passed += 1
else:
    failed += 1
if check("HTML has file browser UI", "file-browser" in html and "fb-drives" in html):
    passed += 1
else:
    failed += 1

# Test 4: API endpoints
print("\n=== API Endpoints ===")
for endpoint in ['/projects', '/sessions', '/memory/banks', '/agents', '/models', '/rules', '/config', '/fs/drives']:
    r = requests.get(f"{BASE}/api{endpoint}", timeout=5)
    if check(f"GET /api{endpoint}", r.status_code == 200):
        passed += 1
    else:
        failed += 1

# Test 5: No 404s
print("\n=== Static Assets ===")
for path in ['/', '/static/style.css', '/static/js/app.js']:
    r = requests.get(f"{BASE}{path}", timeout=5)
    if check(f"GET {path} (no 404)", r.status_code != 404):
        passed += 1
    else:
        failed += 1

print(f"\n=== Results: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
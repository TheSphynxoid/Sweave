"""Comprehensive test of the new clean UI."""
import requests
import sys

PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"

def check(name, ok, details=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" - {details}" if details else ""))
    return ok

print(f"=== Testing Sweave v3 at {BASE} ===\n")

passed = 0
failed = 0

# Test 1: Page loads with all elements
r = requests.get(f"{BASE}/", timeout=5)
html = r.text

checks = [
    ('id="app"', "App container"),
    ('id="loader"', "Loader"),
    ('id="welcome"', "Welcome screen"),
    ('id="project-link"', "Topbar project link"),
    ('id="session-link"', "Topbar session link"),
    ('id="new-session-btn"', "New session button"),
    ('id="sidebar-toggle"', "Sidebar toggle"),
    ('class="nav-item"', "Nav items class"),
    ('data-tab="chat"', "Chat tab"),
    ('data-tab="children"', "Children tab"),
    ('data-tab="agents"', "Agents tab"),
    ('data-tab="memory"', "Memory tab"),
    ('data-tab="settings"', "Settings tab"),
    ('id="tab-chat"', "Chat panel"),
    ('id="tab-children"', "Children panel"),
    ('id="tab-agents"', "Agents panel"),
    ('id="tab-memory"', "Memory panel"),
    ('id="tab-settings"', "Settings panel"),
    ('id="project-modal"', "Project modal"),
    ('id="fb-drives"', "File browser drives container"),
    ('id="error-box"', "Error box (debug)"),
]
for needle, name in checks:
    if check(name, needle in html):
        passed += 1
    else:
        failed += 1

# Test 2: CSS loads
r = requests.get(f"{BASE}/static/style.css", timeout=5)
css = r.text
if check("CSS has --bg variable", "--bg:" in css):
    passed += 1
else:
    failed += 1
if check("CSS has .nav-item styles", ".nav-item" in css and "active" in css):
    passed += 1
else:
    failed += 1
if check("CSS has fullscreen layout", "100vh" in css and "100vw" in css):
    passed += 1
else:
    failed += 1

# Test 3: JS loads
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
js = r.text
js_checks = [
    ('function switchTab', "switchTab function"),
    ("forEach(item => {", "forEach on items"),
    ("addEventListener('click'", "click listener"),
    ('switchTab(tab)', "switchTab called with tab arg"),
    ('setupEventListeners', "setupEventListeners function"),
    ("if (document.readyState === 'loading')", "DOM ready check"),
    ('console.log', "Debug logging"),
    ('window.addEventListener(\'error\'', "Global error handler"),
]
for needle, name in js_checks:
    if check(name, needle in js):
        passed += 1
    else:
        failed += 1

# Test 4: API endpoints
print("\n=== API Tests ===")
api_checks = [
    ('/projects', "List projects"),
    ('/sessions', "List sessions"),
    ('/memory/banks', "Memory banks"),
    ('/agents', "List agents"),
    ('/models', "Models"),
    ('/rules', "Routing rules"),
    ('/config', "Config"),
    ('/fs/drives', "File system drives"),
]
for path, name in api_checks:
    r = requests.get(f"{BASE}/api{path}", timeout=5)
    if check(f"GET /api{path}", r.status_code == 200):
        passed += 1
    else:
        failed += 1

# Test 5: File browser works
print("\n=== File Browser Tests ===")
r = requests.get(f"{BASE}/api/fs/drives", timeout=5)
if check("Drives endpoint", r.status_code == 200):
    passed += 1
    data = r.json()
    print(f"    Found {len(data.get('drives', []))} drives")
else:
    failed += 1

r = requests.get(f"{BASE}/api/fs/list", params={"path": "C:\\"}, timeout=5)
if check("List C:\\", r.status_code == 200):
    passed += 1
    data = r.json()
    print(f"    C:\\ has {data.get('total')} entries")
else:
    failed += 1

print(f"\n=== Results: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
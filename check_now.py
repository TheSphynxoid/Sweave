import requests
import sys

PORT = 8091
BASE = f"http://127.0.0.1:{PORT}"

print(f"Verifying server at {BASE}")

# Check main page
r = requests.get(f"{BASE}/", timeout=5)
print(f"GET / -> {r.status_code}, {len(r.text)} bytes")
if "project-welcome" in r.text:
    print("  [PASS] Project welcome screen present")
if "sidebar-project-name" in r.text:
    print("  [PASS] Sidebar project context present")
if "session-switcher" in r.text:
    print("  [PASS] Session switcher present")
if "children-panel" in r.text:
    print("  [PASS] Children (specialist agent) panel present")

# Check JS has project/session handling
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
js = r.text
if "switchProject" in js:
    print("  [PASS] switchProject function in app.js")
if "createNewSession" in js:
    print("  [PASS] createNewSession function in app.js")
if "loadMemoryBanks" in js:
    print("  [PASS] loadMemoryBanks function in app.js")
if "renderSessionMessages" in js:
    print("  [PASS] renderSessionMessages function in app.js")
if "renderChildrenList" in js:
    print("  [PASS] renderChildrenList function in app.js")

# Check API has project/session endpoints
r = requests.get(f"{BASE}/static/js/api.js", timeout=5)
api = r.text
for endpoint in ["listProjects", "createProject", "setActiveProject",
                 "listSessions", "createSession", "setActiveSession",
                 "getMemoryBanks", "addMessage"]:
    if endpoint in api:
        print(f"  [PASS] API.{endpoint} in api.js")

# Test API endpoints
print("\nAPI Endpoint Tests:")
for endpoint, method in [("/api/projects", "GET"), ("/api/sessions", "GET"),
                         ("/api/memory/banks", "GET")]:
    r = requests.request(method, f"{BASE}{endpoint}", timeout=5)
    print(f"  [{'PASS' if r.status_code == 200 else 'FAIL'}] {method} {endpoint} -> {r.status_code}")

print(f"\nServer is running at: {BASE}")
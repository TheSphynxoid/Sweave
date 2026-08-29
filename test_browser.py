import requests
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8094
BASE = f"http://127.0.0.1:{PORT}"

def check(name, ok, details=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" - {details}" if details else ""))
    return ok

print(f"=== Testing File Browser API at {BASE} ===\n")

passed = 0
failed = 0

# Test 1: Get drives
r = requests.get(f"{BASE}/api/fs/drives", timeout=5)
if check("GET /api/fs/drives", r.status_code == 200, str(r.json())):
    passed += 1
else:
    failed += 1

# Test 2: List current dir
r = requests.get(f"{BASE}/api/fs/list", params={"path": "."}, timeout=5)
if check("GET /api/fs/list (current dir)", r.status_code == 200):
    data = r.json()
    print(f"    Path: {data.get('path')}")
    print(f"    Entries: {len(data.get('entries', []))}")
    passed += 1
else:
    failed += 1
    print(f"    Error: {r.text}")

# Test 3: List Windows C: drive
r = requests.get(f"{BASE}/api/fs/list", params={"path": "C:\\"}, timeout=5)
if check("GET /api/fs/list (C:\\)", r.status_code == 200):
    data = r.json()
    print(f"    Path: {data.get('path')}")
    print(f"    Total entries: {data.get('total')}")
    passed += 1
else:
    failed += 1
    print(f"    Error: {r.text}")

# Test 4: List a specific subdirectory (C:\Users)
r = requests.get(f"{BASE}/api/fs/list", params={"path": "C:\\Users"}, timeout=5)
if check("GET /api/fs/list (C:\\Users)", r.status_code == 200):
    data = r.json()
    print(f"    Path: {data.get('path')}")
    print(f"    Entries: {[e['name'] for e in data.get('entries', [])[:5]]}")
    passed += 1
else:
    failed += 1

# Test 5: Validate a real path
r = requests.post(f"{BASE}/api/fs/validate", json={"path": "C:\\Users"}, timeout=5)
if check("POST /api/fs/validate (C:\\Users)", r.status_code == 200):
    data = r.json()
    print(f"    Valid: {data.get('valid')}, Name: {data.get('name')}")
    passed += 1
else:
    failed += 1

# Test 6: Validate an invalid path
r = requests.post(f"{BASE}/api/fs/validate", json={"path": "C:\\nonexistent\\fake"}, timeout=5)
if check("POST /api/fs/validate (invalid path)", r.status_code == 200):
    data = r.json()
    if not data.get('valid'):
        passed += 1
    else:
        print("    [WARN] Path was marked valid but shouldn't be")
        failed += 1
else:
    failed += 1

# Test 7: Create a new test directory
import tempfile
import os
test_dir = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'sweave_test_browser')
try:
    r = requests.post(f"{BASE}/api/fs/create", json={"path": test_dir, "name": "new_test_project"}, timeout=5)
    if check("POST /api/fs/create (new folder)", r.status_code == 200):
        data = r.json()
        print(f"    Created: {data.get('path')}")
        if os.path.exists(data.get('path', '')):
            os.rmdir(data['path'])
        passed += 1
    else:
        print(f"    Error: {r.text}")
        failed += 1
except Exception as e:
    print(f"    Setup error: {e}")
    failed += 1

# Test 8: Page loads correctly
r = requests.get(f"{BASE}/", timeout=5)
html = r.text
if check("Index has file-browser", "file-browser" in html and "fb-drives" in html):
    passed += 1
else:
    failed += 1

print(f"\n=== Results: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
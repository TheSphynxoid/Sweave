import requests
import subprocess
import time
import os
from pathlib import Path

log = open("web_test.log", "w")
err = open("web_test_err.log", "w")
process = subprocess.Popen(
    ["python", "-m", "sweave.cli.main", "web", "--host", "127.0.0.1", "--port", "9095"],
    stdout=log, stderr=err,
    cwd=Path.cwd()
)

for i in range(30):
    time.sleep(1)
    try:
        r = requests.get('http://127.0.0.1:9095/api/agents', timeout=2)
        if r.status_code == 200:
            break
    except:
        pass

BASE = 'http://127.0.0.1:9095'

try:
    # Create a temp project folder
    test_dir = Path.cwd() / "test_project_folder"
    test_dir.mkdir(exist_ok=True)

    # Test 1: Create project
    print("\n[1] Creating project...")
    r = requests.post(f"{BASE}/api/projects", json={
        "name": "test-project",
        "path": str(test_dir),
        "description": "Test project for verification"
    }, timeout=5)
    if r.status_code == 200:
        print(f"  [PASS] Project created: {r.json()['project']['name']}")
    else:
        print(f"  [FAIL] {r.status_code}: {r.text}")

    # Test 2: List projects
    print("\n[2] Listing projects...")
    r = requests.get(f"{BASE}/api/projects", timeout=5)
    if r.status_code == 200:
        projects = r.json()['projects']
        print(f"  [PASS] Found {len(projects)} projects: {[p['name'] for p in projects]}")
    else:
        print(f"  [FAIL] {r.status_code}")

    # Test 3: Get active project
    print("\n[3] Getting active project...")
    r = requests.get(f"{BASE}/api/projects/active", timeout=5)
    if r.status_code == 200 and r.json():
        print(f"  [PASS] Active project: {r.json()['name']}")
    else:
        print(f"  [FAIL] {r.status_code}: {r.text}")

    # Test 4: Create session
    print("\n[4] Creating session...")
    r = requests.post(f"{BASE}/api/sessions", json={
        "name": "Test Session",
        "project_name": "test-project"
    }, timeout=5)
    if r.status_code == 200:
        session = r.json()['session']
        print(f"  [PASS] Session created: {session['id'][:20]}...")
    else:
        print(f"  [FAIL] {r.status_code}: {r.text}")
        session = None

    if session:
        # Test 5: Get session
        print("\n[5] Getting session details...")
        r = requests.get(f"{BASE}/api/sessions/{session['id']}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"  [PASS] Session: {data['name']} ({len(data['messages'])} messages, {len(data['children'])} children)")

        # Test 6: Add message
        print("\n[6] Adding message to session...")
        r = requests.post(f"{BASE}/api/sessions/{session['id']}/messages", json={
            "role": "user",
            "content": "Build a REST API with auth"
        }, timeout=5)
        if r.status_code == 200:
            print(f"  [PASS] Message added")

        # Test 7: Get memory banks
        print("\n[7] Getting memory banks...")
        r = requests.get(f"{BASE}/api/memory/banks", timeout=5)
        if r.status_code == 200:
            banks = r.json()['banks']
            print(f"  [PASS] Found {len(banks)} banks: {[b['id'] for b in banks]}")

    # Test 8: Delete project
    print("\n[8] Cleaning up test project...")
    r = requests.delete(f"{BASE}/api/projects/test-project", timeout=5)
    if r.status_code == 200:
        print(f"  [PASS] Project deleted")

    # Cleanup
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)

finally:
    process.terminate()
    try: process.wait(timeout=5)
    except: process.kill()
    log.close()
    err.close()
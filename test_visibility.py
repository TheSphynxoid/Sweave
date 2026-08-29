"""Quick test to verify the app becomes visible after loading."""
import requests
import re

PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"

print(f"=== Testing App Visibility at {BASE} ===\n")

# Get the page
r = requests.get(f"{BASE}/", timeout=5)
html = r.text

# Check that the app div starts hidden
if 'id="app" class="app hidden"' in html:
    print("[OK] app div starts with 'hidden' class (correct)")
else:
    print("[FAIL] app div does not start hidden")

# Get the JS
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
js = r.text

# Check that the JS removes the hidden class
if "app.classList.remove('hidden')" in js:
    print("[OK] JS removes hidden class from app")
else:
    print("[FAIL] JS does not remove hidden class from app - THIS IS THE BUG!")

# Check for the specific fix
if 'app.classList.remove(\'hidden\')' in js or 'app.classList.remove("hidden")' in js or "app.classList.remove('hidden')" in js:
    print("[OK] The fix is in place")
else:
    # The fix should be inside the setTimeout
    if "loader" in js and "app" in js:
        # Find the context
        idx = js.find("loader.remove()")
        if idx > 0:
            context = js[idx:idx+500]
            if "hidden" in context:
                print(f"[OK] hidden class is removed in loader context")
            else:
                print(f"[WARN] hidden not in loader context")
                print(f"Context: {context[:200]}")

# Also verify showProjectMode and showWelcome don't accidentally hide app
if "function showProjectMode" in js:
    idx = js.find("function showProjectMode")
    end = js.find("function", idx + 10)
    func = js[idx:end] if end > 0 else js[idx:idx+300]
    if "$('app').classList.add('hidden')" in func:
        print("[FAIL] showProjectMode adds hidden to app!")
    else:
        print("[OK] showProjectMode doesn't hide app")
    print(f"  showProjectMode: {func[:200]}")

if "function showWelcome" in js:
    idx = js.find("function showWelcome")
    end = js.find("function", idx + 10)
    func = js[idx:end] if end > 0 else js[idx:idx+300]
    if "$('app').classList.add('hidden')" in func:
        print("[FAIL] showWelcome adds hidden to app!")
    else:
        print("[OK] showWelcome doesn't hide app")
    print(f"  showWelcome: {func[:200]}")

# Test all API endpoints still work
print("\n=== Quick API test ===")
for path in ['/projects', '/sessions', '/memory/banks', '/agents', '/fs/drives']:
    r = requests.get(f"{BASE}/api{path}", timeout=5)
    print(f"  [{'PASS' if r.status_code == 200 else 'FAIL'}] /api{path}")
"""Debug why sidebar nav doesn't work. Simulate a browser environment to test."""
import requests
import re
import sys

PORT = 8097
BASE = f"http://127.0.0.1:{PORT}"

# Get the page
r = requests.get(f"{BASE}/", timeout=5)
html = r.text

# Check for nav items in HTML
nav_items = re.findall(r'<button class="nav-item[^"]*" data-tab="([^"]+)"', html)
print(f"Nav items in HTML: {nav_items}")

# Get the JS
r = requests.get(f"{BASE}/static/js/app.js", timeout=5)
js = r.text

# Check for the click handler
if "nav-item').forEach" in js:
    print("[OK] nav-item click handler present in JS")
else:
    print("[FAIL] nav-item click handler NOT found in JS")

# Check the function being called
if "switchTab(item.dataset.tab)" in js:
    print("[OK] switchTab function called with item.dataset.tab")
else:
    print("[FAIL] switchTab call not found")

# Check switchTab function definition
if "function switchTab(tab)" in js:
    print("[OK] switchTab function defined")
else:
    print("[FAIL] switchTab function NOT defined")

# Check for tab-panel class manipulation
if ".classList.toggle('active', panel.id === `${tab}-panel`)" in js:
    print("[OK] tab-panel class manipulation present")
else:
    print("[FAIL] tab-panel class manipulation NOT found")

# Check for event listener timing
if "DOMContentLoaded" in js:
    print("[OK] DOMContentLoaded listener present")
else:
    print("[FAIL] DOMContentLoaded listener NOT found")

# Check init function
if "async function init()" in js or "function init()" in js:
    print("[OK] init function defined")
else:
    print("[FAIL] init function NOT defined")

# Check that setupEventListeners is called
if "setupEventListeners()" in js:
    # Find the call
    idx = js.find("setupEventListeners()")
    line = js[:idx].count('\n') + 1
    print(f"[OK] setupEventListeners() called at line ~{line}")
else:
    print("[FAIL] setupEventListeners() NOT called")

# Print the structure around setupEventListeners
if "setupEventListeners()" in js:
    idx = js.find("setupEventListeners()")
    # Find the start of the line
    line_start = js.rfind('\n', 0, idx) + 1
    line_end = js.find('\n', idx)
    print(f"  Context: ...{js[max(0,line_start-30):line_end]}...")

# Look for where setupEventListeners is called - is it in init or before?
init_match = re.search(r'function init\(\)\s*{([^}]*(?:{[^}]*}[^}]*)*)}', js)
if init_match:
    init_body = init_match.group(1)
    if "setupEventListeners()" in init_body:
        print("[OK] setupEventListeners() called inside init()")
    else:
        print("[FAIL] setupEventListeners() NOT called inside init()")

# Check if there's an error handler
if "window.addEventListener('error'" in js:
    print("[OK] Global error handler present")
else:
    print("[FAIL] No global error handler")
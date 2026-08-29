#!/usr/bin/env python3
"""Verify the HTML structure of the Sweave web UI."""

import requests
import sys

def verify_html():
    """Check that the HTML has the expected structure."""
    print("Verifying Sweave HTML structure...")

    # Start server
    import subprocess
    import time

    log = open("web.log", "w")
    err = open("web_err.log", "w")
    process = subprocess.Popen(
        ["python", "-m", "sweave.cli.main", "web", "--host", "127.0.0.1", "--port", "9092"],
        stdout=log, stderr=err
    )

    base_url = "http://127.0.0.1:9092"
    for i in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{base_url}/api/agents", timeout=2)
            if r.status_code == 200:
                break
        except:
            pass

    try:
        # Get index
        response = requests.get(f"{base_url}/", timeout=5)
        html = response.text

        # Check for key elements
        checks = [
            ("<!DOCTYPE html>", "HTML5 doctype"),
            ('id="app"', "App container"),
            ('id="chat-messages"', "Chat messages container"),
            ('id="chat-input"', "Chat input field"),
            ('id="send-btn"', "Send button"),
            ('id="agents-grid"', "Agents grid"),
            ('id="tasks-tbody"', "Tasks table body"),
            ('id="worktrees-tbody"', "Worktrees table body"),
            ('id="recall-query"', "Recall query input"),
            ('id="reflect-query"', "Reflect query input"),
            ('id="retain-content"', "Retain content input"),
            ('id="create-agent-modal"', "Create agent modal"),
            ('id="run-task-modal"', "Run task modal"),
            ('id="create-agent-form"', "Create agent form"),
            ('id="agent-model"', "Agent model select"),
            ('id="agent-prompt"', "Agent prompt textarea"),
            ('id="routing-rules-list"', "Routing rules list"),
            ('id="theme-presets"', "Theme presets"),
            ('href="/static/style.css"', "Style sheet linked"),
            ('src="/static/js/app.js"', "Main JS linked"),
            ('WebSocketManager', "WebSocket in main JS"),
            ('agentsGrid', "Agents grid handler"),
            ('renderAgents', "Agent rendering function"),
        ]

        passed = 0
        failed = 0

        for needle, description in checks:
            if needle in html:
                print(f"  [PASS] {description}")
                passed += 1
            else:
                print(f"  [FAIL] {description} (not found: {needle[:50]})")
                failed += 1

        # Check JS files
        print("\nChecking JavaScript modules...")
        js_checks = [
            ("/static/js/app.js", "import", "ES module imports"),
            ("/static/js/api.js", "export const API", "API exports"),
            ("/static/js/theme.js", "export class ThemeManager", "Theme manager export"),
            ("/static/js/websocket.js", "export class WebSocketManager", "WebSocket manager export"),
            ("/static/js/modal.js", "export class ModalManager", "Modal manager export"),
            ("/static/js/notification.js", "export class NotificationManager", "Notification manager export"),
        ]

        for path, needle, desc in js_checks:
            r = requests.get(f"{base_url}{path}", timeout=5)
            if r.status_code == 200 and needle in r.text:
                print(f"  [PASS] {desc}")
                passed += 1
            else:
                print(f"  [FAIL] {desc}")
                failed += 1

        # Check CSS
        print("\nChecking CSS features...")
        css_response = requests.get(f"{base_url}/static/style.css", timeout=5)
        css = css_response.text
        css_checks = [
            ("--bg:", "CSS variables"),
            ("--red:", "Red accent variable"),
            ("@font-face", "Custom fonts"),
            (".agent-card", "Agent card styles"),
            (".modal", "Modal styles"),
            (".chat-messages", "Chat message styles"),
            (".memory-tabs", "Memory tab styles"),
            (".routing-rule-item", "Routing rule styles"),
            (".theme-preset", "Theme preset styles"),
            ("@keyframes", "Animations"),
            ("::-webkit-scrollbar", "Custom scrollbar"),
        ]

        for needle, desc in css_checks:
            if needle in css:
                print(f"  [PASS] {desc}")
                passed += 1
            else:
                print(f"  [FAIL] {desc}")
                failed += 1

        print(f"\n{'='*60}")
        print(f"HTML VERIFICATION: {passed} passed, {failed} failed")
        print(f"{'='*60}")

        return failed == 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except:
            process.kill()
        log.close()
        err.close()

if __name__ == "__main__":
    if verify_html():
        print("\n[PASS] All verification checks passed!")
        sys.exit(0)
    else:
        print("\n[FAIL] Some checks failed")
        sys.exit(1)
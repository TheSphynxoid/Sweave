#!/usr/bin/env python3
"""Stop the detached Sweave web server."""

import os
import sys
import signal
from pathlib import Path


def main():
    pid_file = Path("web.pid")
    if not pid_file.exists():
        print("No web.pid file found. Server may not be running.")
        return 1

    pid = int(pid_file.read_text().strip())
    print(f"Stopping server (PID {pid})...")

    try:
        if sys.platform == "win32":
            import subprocess
            # /T kills the whole process tree: the server's opencode
            # serves (and their MCP children) must not survive the
            # server -- orphaned serves were a 4GB leak (2026-09-10:
            # 13 stale serves across restarts).
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
        else:
            os.kill(pid, signal.SIGTERM)
            # Wait for graceful shutdown
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

        pid_file.unlink()
        print("Server stopped")
    except ProcessLookupError:
        print(f"PID {pid} not found (already stopped?)")
        pid_file.unlink()
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
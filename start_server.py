#!/usr/bin/env python3
"""
Start the Sweave web server as a proper background daemon.
Works on Windows (using CREATE_NEW_PROCESS_GROUP) and Linux.
"""

import sys
import subprocess
import os
import time
from pathlib import Path

# Windows-specific flags for detaching a process
if sys.platform == "win32":
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
else:
    flags = 0


def main():
    host = "127.0.0.1"
    port = 8080

    if len(sys.argv) >= 2:
        port = int(sys.argv[1])
    if len(sys.argv) >= 3:
        host = sys.argv[2]

    print(f"Starting Sweave server on {host}:{port} (detached)...")

    # Change to project root
    project_root = Path(__file__).parent.resolve()
    os.chdir(project_root)

    log_file = open("web.log", "w")
    err_file = open("web_err.log", "w")

    # Start server detached
    if sys.platform == "win32":
        process = subprocess.Popen(
            [sys.executable, "-m", "sweave.cli.main", "web",
             "--host", host, "--port", str(port)],
            stdout=log_file,
            stderr=err_file,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    else:
        process = subprocess.Popen(
            [sys.executable, "-m", "sweave.cli.main", "web",
             "--host", host, "--port", str(port)],
            stdout=log_file,
            stderr=err_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    # Write PID file
    with open("web.pid", "w") as f:
        f.write(str(process.pid))

    print(f"Server started with PID {process.pid}")
    print(f"Logs: web.log, web_err.log")
    print(f"Open http://{host}:{port} in your browser")

    # Don't wait - let it run detached
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""MCP server entry point for ``python -m sweave.mcp`` (stdio transport)."""
from __future__ import annotations

import asyncio
import sys

from sweave.mcp import main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

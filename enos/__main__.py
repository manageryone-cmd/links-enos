"""Allow ``python3 -m enos`` as a shorthand for the stdio MCP server."""

from __future__ import annotations

import sys

from enos.mcp_server import main

if __name__ == "__main__":
    sys.exit(main())

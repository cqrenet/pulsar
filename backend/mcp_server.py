#!/usr/bin/env python3
"""PULSAR MCP Server — stdio transport for local development.

For the HTTP/SSE version (production, behind OIDC auth), see routes/mcp.py.

Usage:
    python mcp_server.py

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "pulsar": {
          "command": "python",
          "args": ["/path/to/pulsar/backend/mcp_server.py"],
          "env": {"MONGO_URI": "mongodb://..."}
        }
      }
    }

WARNING: This stdio server has no authentication layer.
Only run it in trusted environments (e.g. local Claude Desktop).
Ensure the MongoDB connection uses authenticated credentials.
"""

import os
import sys

# Ensure backend modules are importable when run standalone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_tools import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio")

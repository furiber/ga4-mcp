"""Horizon entrypoint (`main.py:mcp`): the plain MCP SDK server, run by Horizon's fastmcp CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ga4_mcp.server import mcp  # noqa: E402

__all__ = ["mcp"]

"""Vercel entrypoint: exposes the remote (Google OAuth) MCP server as the ASGI `app`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ga4_mcp.remote import create_app  # noqa: E402

app = create_app()

"""Horizon entrypoint (`main.py:mcp`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ga4_mcp.server import mcp  # noqa: E402

__all__ = ["mcp"]

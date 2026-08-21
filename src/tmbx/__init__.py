"""Timebox core and MCP server.

This package is clean-room: it must never import from ``fateforger``.
The reverse direction is intentional and is how the legacy agent writes
to the shared journal.
"""

from __future__ import annotations

__all__: list[str] = []

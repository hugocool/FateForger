"""
Constraint Memory Tools Configuration - MCP server parameters and tool loader.

Provides a standardized function for loading Constraint Memory MCP tools for AutoGen agents.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools


def _constraint_server_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH", "")
    paths = [str(root / "src"), str(root)]
    if pythonpath:
        paths.append(pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


async def get_constraint_mcp_tools(timeout: float = 5.0):  # type: ignore
    """
    Return the list of MCP tools for the constraint memory store using stdio transport.
    """

    root = Path(__file__).resolve().parents[3]
    server_path = root / "scripts" / "constraint_mcp_server.py"
    params = StdioServerParams(
        command=sys.executable,
        args=[str(server_path)],
        env=_constraint_server_env(root),
        cwd=str(root),
        read_timeout_seconds=timeout,
    )
    return await mcp_server_tools(params)

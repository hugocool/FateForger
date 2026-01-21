"""
Constraint Memory Tools Configuration - MCP server parameters and tool loader.

Provides a standardized function for loading Constraint Memory MCP tools for AutoGen agents.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools


def build_constraint_server_env(root: Path) -> dict[str, str]:
    """Build environment variables for the constraint MCP server process."""
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH", "")
    paths = [str(root / "src"), str(root)]
    if pythonpath:
        paths.append(pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def resolve_constraint_repo_root(start: Path | None = None) -> Path:
    """Resolve the repo root by walking upward to find the constraint MCP server."""
    cursor = (start or Path(__file__).resolve()).resolve()
    if cursor.is_file():
        cursor = cursor.parent
    while True:
        if (cursor / "scripts" / "constraint_mcp_server.py").exists():
            return cursor
        if cursor.parent == cursor:
            return (start or Path(__file__).resolve()).parent
        cursor = cursor.parent


async def get_constraint_mcp_tools(timeout: float = 30.0):  # type: ignore
    """
    Return the list of MCP tools for the constraint memory store using stdio transport.
    """

    root = resolve_constraint_repo_root()
    server_path = root / "scripts" / "constraint_mcp_server.py"
    params = StdioServerParams(
        command=sys.executable,
        args=[str(server_path)],
        env=build_constraint_server_env(root),
        cwd=str(root),
        read_timeout_seconds=timeout,
    )
    return await mcp_server_tools(params)


__all__ = ["build_constraint_server_env", "get_constraint_mcp_tools", "resolve_constraint_repo_root"]

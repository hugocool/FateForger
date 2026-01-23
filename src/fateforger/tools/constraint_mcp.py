"""
Constraint Memory Tools Configuration - MCP server parameters and tool loader.

Provides a standardized function for loading Constraint Memory MCP tools for AutoGen agents.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable

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


def _sanitize_tool_name(name: str) -> str:
    """Convert MCP tool names into OpenAI-safe tool names."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _dedupe_tool_names(names: Iterable[str]) -> Dict[str, str]:
    """Return a stable mapping of original tool names to safe, unique names."""
    mapping: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    for name in names:
        base = _sanitize_tool_name(name)
        count = seen.get(base, 0)
        safe = f"{base}_{count}" if count else base
        seen[base] = count + 1
        mapping[name] = safe
    return mapping


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
    tools = await mcp_server_tools(params)
    name_map = _dedupe_tool_names(tool.name for tool in tools)
    for tool in tools:
        safe_name = name_map.get(tool.name, tool.name)
        if safe_name != tool.name:
            tool._name = safe_name
    return tools


__all__ = ["build_constraint_server_env", "get_constraint_mcp_tools", "resolve_constraint_repo_root"]

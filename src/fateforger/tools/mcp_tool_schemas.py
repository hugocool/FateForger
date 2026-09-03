"""Make remote MCP tool schemas fit the converter AutoGen ships.

``autogen_ext.tools.mcp.mcp_server_tools`` turns each tool's
``inputSchema`` into a Pydantic model with
``autogen_core.utils.schema_to_pydantic_model``. That converter (0.7.5,
the latest release at 2026-09-02) reads ``"type"`` as a single string;
JSON Schema also allows a list -- ``{"type": ["object", "null"]}`` is the
usual way to say "a nullable object" -- and the converter answers that
with ``TypeError: unhashable type: 'list'``. notion-mcp uses the list form
six times (``rich_text[].text.link``), so registration crashed on every
startup and, because the server is optional, was logged as if Notion were
merely down (#257).

Two things live here:

* ``normalise_nullable_types`` -- rewrite the shorthand into what the
  converter can express. Nullability becomes "not required" (the only
  Optional the converter knows). A list of two *real* types is left
  alone: there is no honest single type to collapse it to, so it goes on
  to fail loudly rather than be silently narrowed.
* ``streamable_http_tools`` -- the same steps as ``mcp_server_tools`` for
  a streamable-HTTP server, with the rewrite in between listing and
  adapting.

This operates on JSON Schema keywords the server minted (``type``,
``properties``, ``required``, ``items``). Nothing here reads user text.
"""

from __future__ import annotations

import copy
from typing import Any

_NULL = "null"


def normalise_nullable_types(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` with list-valued ``type`` collapsed.

    ``["X", "null"]`` -> ``"X"`` and the property is dropped from its
    parent's ``required``; ``["null"]`` -> ``"null"``; a list of several
    non-null types is left as it was. Walks ``properties``, ``items``,
    ``additionalProperties``, ``$defs``/``definitions``, and the
    ``allOf``/``anyOf``/``oneOf`` arrays.
    """

    return _normalise(copy.deepcopy(schema))


def _normalise(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalise(item) for item in node]
    if not isinstance(node, dict):
        return node

    properties = node.get("properties")
    if isinstance(properties, dict):
        nullable_keys = [
            key
            for key, prop in properties.items()
            if isinstance(prop, dict) and _lists_null(prop.get("type"))
        ]
        if nullable_keys and isinstance(node.get("required"), list):
            node["required"] = [
                key for key in node["required"] if key not in nullable_keys
            ]

    node_type = node.get("type")
    if isinstance(node_type, list):
        remaining = [t for t in node_type if t != _NULL]
        if not remaining:
            node["type"] = _NULL
        elif len(remaining) == 1:
            node["type"] = remaining[0]
        # else: a real union; leave it for the converter to refuse.

    for key, value in list(node.items()):
        if isinstance(value, (dict, list)):
            node[key] = _normalise(value)
    return node


def _lists_null(node_type: Any) -> bool:
    return isinstance(node_type, list) and _NULL in node_type


async def streamable_http_tools(server_params: Any) -> list:
    """``mcp_server_tools`` for a streamable-HTTP server, with each tool's
    ``inputSchema`` normalised before the adapter converts it."""

    from autogen_ext.tools.mcp import StreamableHttpMcpToolAdapter
    from autogen_ext.tools.mcp._session import create_mcp_server_session

    async with create_mcp_server_session(server_params) as session:
        await session.initialize()
        listed = await session.list_tools()

    adapters = []
    for tool in listed.tools:
        tool.inputSchema = normalise_nullable_types(tool.inputSchema)
        adapters.append(StreamableHttpMcpToolAdapter(server_params=server_params, tool=tool))
    return adapters


__all__ = ["normalise_nullable_types", "streamable_http_tools"]

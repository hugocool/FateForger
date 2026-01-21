#!/usr/bin/env python3
"""Debug calendar MCP connectivity."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from dotenv import load_dotenv

load_dotenv()

from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams


async def main():
    params = StreamableHttpServerParams(url="http://localhost:3000/mcp", timeout=10.0)
    wb = McpWorkbench(params)

    # List available tools
    tools = await wb.list_tools()
    print("Available tools:")
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else getattr(t, "name", str(t))
        print(f"  - {name}")

    # Try list-calendars if available
    print()
    try:
        result = await wb.call_tool("list-calendars", arguments={})
        print("Calendars:", result)
    except Exception as e:
        print(f"list-calendars failed: {e}")

    # Try list-events with verbose output
    print()
    try:
        result = await wb.call_tool(
            "list-events",
            arguments={
                "calendarId": "primary",
                "timeMin": "2026-01-20T00:00:00Z",
                "timeMax": "2026-01-22T00:00:00Z",
            },
        )
        print("Events result:", result)
    except Exception as e:
        print(f"list-events failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(main())
    asyncio.run(main())
    asyncio.run(main())
    asyncio.run(main())
    asyncio.run(main())

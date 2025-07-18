---
title: MCP Tools
---
# MCP Tools

`McpWorkbench` connects to the calendar service defined in `docker-compose.yml` under the `calendar-mcp` service. Example usage:

```python
from productivity_bot.autogen_planner import McpWorkbench
mcp = McpWorkbench("http://localhost:4000/jsonrpc")
```

Available methods include `list_events`, `insert_event`, and `update_event`.

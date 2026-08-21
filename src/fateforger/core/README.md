# core

## Status

- Implemented: Graphiti is the active durable-memory runtime path when `TIMEBOXING_MEMORY_BACKEND=graphiti`.
- Tested: the VS Code local Slack bot debug tasks now bring up `neo4j` and `graphiti-mcp`, and the Python debug launch config pins the local Neo4j endpoint while inheriting the Graphiti MCP URL from `.env`.

Runtime startup logs include git provenance fields (`branch`, `commit`, `tag`, `dirty`) to help correlate observed behavior with the exact running code revision.

When `TIMEBOXING_MEMORY_BACKEND=graphiti` is active, startup also logs the durable-memory runtime identity:

- `graphiti_store_backend=neo4j`
- `graphiti_mcp_server_url`
- `graphiti_neo4j_uri`

Startup MCP dependency checks now treat `graphiti-mcp` as a required server whenever Graphiti is enabled and fail fast if the configured MCP endpoint does not expose the required tool surface (`add_memory`, `get_episodes`).

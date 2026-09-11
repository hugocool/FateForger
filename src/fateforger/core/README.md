# core

## Status

- Implemented: Graphiti is the active durable-memory runtime path when `TIMEBOXING_MEMORY_BACKEND=graphiti`.
- Tested: the VS Code local Slack bot debug tasks now bring up `neo4j` and `graphiti-mcp`, and the Python debug launch config pins the local Neo4j endpoint while inheriting the Graphiti MCP URL from `.env`.

The `TIMEBOXING_MEMORY_BACKEND` setting name predates the 2026-09-09 legacy-agent
retirement (`refactor: retire TimeboxingFlowAgent and the 34 modules only it
reached`); the timeboxing coordinator it names is deleted. The Graphiti startup
checks below now serve `runtime.py` itself and `fateforger.agents.tasks.defaults_memory`
(tasks' defaults memory), which read `graphiti_constraint_memory.py` /
`constraint_record_memory.py` through `settings.timeboxing_memory_backend` —
see `src/fateforger/agents/timeboxing/README.md` for that backend's current
callers.

Runtime startup logs include git provenance fields (`branch`, `commit`, `tag`, `dirty`) to help correlate observed behavior with the exact running code revision.

When `TIMEBOXING_MEMORY_BACKEND=graphiti` is active, startup also logs the durable-memory runtime identity:

- `graphiti_store_backend=neo4j`
- `graphiti_mcp_server_url`
- `graphiti_neo4j_uri`

Startup MCP dependency checks now treat `graphiti-mcp` as a required server whenever Graphiti is enabled and fail fast if the configured MCP endpoint does not expose the required tool surface (`add_memory`, `get_episodes`).

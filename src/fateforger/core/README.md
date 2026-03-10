# core

Runtime startup logs include git provenance fields (`branch`, `commit`, `tag`, `dirty`) to help correlate observed behavior with the exact running code revision.

Startup now performs explicit dependency gates before agent runtime creation:
- required MCP endpoint discovery (`calendar-mcp` mandatory; optional endpoints logged as warnings), and
- Graphiti runtime readiness when any configured backend path requires Graphiti.

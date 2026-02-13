# Progress (Updated: 2026-02-07)

## Done

- Live MCP pipeline tested end-to-end: CREATE (6 events), UPDATE (4 patches), DELETE (2 removals) — all verified against real GCal
- Fixed event ID generation: removed underscore from fftb_ prefix (GCal requires only a-v + 0-9)
- Fixed ToolResult parsing: McpWorkbench returns ToolResult.result[].content, not CallToolResult.content

## Doing



## Next

- Wire TBPatch into LLM agent (AutoGen FunctionTool with strict schema)
- Production extraction to src/ modules
- Improve diff_tb_plans to use semantic matching instead of positional

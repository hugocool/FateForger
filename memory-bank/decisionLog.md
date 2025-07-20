# Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-07-19 | Use AutoGen AssistantAgent with MCP integration for calendar operations | User explicitly required "no bypassing AutoGen MCP system" and "no manual HTTP calls". Must use AutoGen's built-in MCP tools, not custom implementations or direct API calls. User was frustrated with repeated violations of this architectural choice. |

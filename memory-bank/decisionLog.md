# Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-07-19 | Use AutoGen AssistantAgent with MCP integration for calendar operations | User explicitly required "no bypassing AutoGen MCP system" and "no manual HTTP calls". Must use AutoGen's built-in MCP tools, not custom implementations or direct API calls. User was frustrated with repeated violations of this architectural choice. |
| 2025-07-20 | Implement Ticket #1: Data contracts and hand-off stub for AutoGen Sequential Workflow | User provided detailed specification for a 7-ticket project to create a structured multi-agent calendar pipeline. Starting with Ticket #1 to establish the foundational data models (PlanDiff, CalendarOp) and sync_plan_to_calendar stub function that will enable LLMs to emit structured Pydantic output and route messages through AutoGen's runtime topics. |

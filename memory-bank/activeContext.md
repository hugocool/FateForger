# Active Context

## Current Goals

- Implementing Ticket #1 of the Sequential Workflow project: Define data contracts & hand-off stub for AutoGen's structured multi-agent calendar pipeline. Creating PlanDiff and CalendarOp Pydantic models plus sync_plan_to_calendar stub for Sequential Workflow pattern with MCP calendar integration.

## USER'S EXPLICIT ARCHITECTURAL CHOICES:
1. **✅ USE AUTOGEN AssistantAgent** - NOT custom classes, NOT simple agents
2. **✅ USE AUTOGEN'S MCP INTEGRATION** - NOT manual HTTP calls, NOT direct REST API
3. **✅ USE MCP WORKBENCH** - The user insisted "no bypassing, you are going to use MCP workbench whether you like it or not"
4. **✅ CONNECT TO REAL CALENDAR DATA** - NOT mock data, NOT fake events
5. **✅ NO HANGING** - All operations must have timeouts

## 🚫 FORBIDDEN SOLUTIONS:
- ❌ Manual HTTP requests to MCP server
- ❌ Custom agent classes instead of AutoGen AssistantAgent  
- ❌ Mock/fake data when user wants real calendar events
- ❌ Bypassing AutoGen MCP system with "simpler" alternatives
- ❌ Suggesting different frameworks than what user chose


## Current Blockers

- MCP server connection issues - need to ensure the server is running and accessible
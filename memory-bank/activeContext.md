# Active Context

## Current Goals

- Completed TRMNL dashboard MVP scaffolding with Command View. Template uses Framework v2, implements 5-minute time quantization UX pattern, and defines comprehensive JSON schema. Ready for backend integration (Python endpoint that merges Calendar/TickTick/Notion/Toggl data).

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
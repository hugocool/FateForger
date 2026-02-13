# Active Context

## Current Goals

- ## Current Focus
- Sync Engine ticket (TICKET_SYNC_ENGINE.md) — Phases 1–6 complete. All 115 unit tests pass. Live MCP + LLM integration validated via notebooks/phase5_integration_test.ipynb.
- **Status:** ✅ Phases 1-5 Complete (23/24 items — AC6 parallelism is deferred stretch goal)
- ## Completed This Session (Phase 5 + 6)
- - Fixed sync_engine.py DeepDiff bug (empty→populated edge case)
- - Fixed patching.py: output_content_type=TBPatch → schema-in-system-prompt (Gemini/OpenRouter compat)
- - Fixed _extract_patch() to strip markdown code fences
- - Created notebooks/phase5_integration_test.ipynb — all 10 sections pass against live GCal MCP + Gemini LLM
- - Created tests/unit/test_patching.py (14 tests)
- - Updated TICKET_SYNC_ENGINE.md, module README, memory bank
- ## Architecture Decisions (Proven)
- - **Schema-in-prompt** for TBPatch: inject TBPatch.model_json_schema() into system prompt instead of output_content_type (which breaks on oneOf with OpenAI/OpenRouter)
- - **Set-diff for creates/deletes** in plan_sync(): DeepDiff only for UPDATE detection on common keys
- - **Gemini via OpenRouter** (google/gemini-3-pro-preview) as the timebox_patcher model
- ## Next Steps (Deferred)
- - AC6: Stage 2 skeleton pre-generation (stretch)
- - Wire CalendarSubmitter into live Slack flow end-to-end
- - Remove trustcall from pyproject.toml (only used in archive notebooks)
- - Add CI for the 115-test sync engine suite
- ## USER'S EXPLICIT ARCHITECTURAL CHOICES:
- 1. **✅ USE AUTOGEN AssistantAgent** — NOT custom classes, NOT simple agents
- 2. **✅ USE AUTOGEN'S MCP INTEGRATION** — NOT manual HTTP calls, NOT direct REST API
- 3. **✅ USE MCP WORKBENCH** — "no bypassing, you are going to use MCP workbench whether you like it or not"
- 4. **✅ CONNECT TO REAL CALENDAR DATA** — NOT mock data, NOT fake events
- 5. **✅ NO HANGING** — All operations must have timeouts
- 6. **✅ USE GEMINI** — google/gemini-3-pro-preview via OpenRouter, NOT GPT-4o-mini
- ## 🚫 FORBIDDEN SOLUTIONS:
- - ❌ Manual HTTP requests to MCP server
- - ❌ Custom agent classes instead of AutoGen AssistantAgent
- - ❌ Mock/fake data when user wants real calendar events
- - ❌ Bypassing AutoGen MCP system with "simpler" alternatives
- - ❌ GPT-4o-mini or any non-Gemini model for timeboxing
- - ❌ output_content_type with discriminated unions (oneOf breaks)

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
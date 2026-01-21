# Timeboxing Agent Notes

## Goals
- Keep the timeboxing flow responsive; never block user replies on durable preference writes.
- Extract session-scoped constraints from user replies (not from generic "start timeboxing" requests).
- Prefetch durable constraints from Notion (via constraint-memory MCP) before Stage 1 so the cache is warm.
- Stage-gating LLMs must not call tools; the coordinator handles all tool IO in background tasks.
- Intent classification must use LLMs (AutoGen agents); do not use regex or keyword matching.

## Background Work
- Local constraint extraction + persistence should run in background tasks.
- Durable (Notion) preference upserts should be fire-and-forget with dedupe + timeout.
- Durable constraint reads should run in the background and be merged with session-scoped constraints.
- Use a separate LLM client for background extraction/intent so it cannot block stage responses.
- Only await pending background tasks if a downstream step strictly needs them (use short timeouts).

## UX Status
- When background work is queued, include a short, friendly status note in stage responses.
- Status notes should reassure the user they can continue without waiting.

## Task Sources
- If TickTick MCP is configured (`TICKTICK_MCP_URL`), stage agents may use TickTick tools to pull tasks.
- Treat task fetch failures as non-blocking; continue the flow with user-provided inputs.

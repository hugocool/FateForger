# Slack Bot Notes

## Timeboxing UX
- Timeboxing sessions are anchored in the dedicated thread/channel; DMs are a control surface.
- User-facing updates must remain fast and readable during background tool work.
- When a timeboxing response includes background status notes, deliver them verbatim.
- Prefer explicit `/timebox` or LLM handoffs for starting sessions; avoid heuristic intent routing.
- Surface background progress (constraint prefetch, calendar fetch) in user-friendly copy, not raw tool logs.
- Route intent decisions via LLM-backed agents or explicit slash commands; avoid regex/keyword intent heuristics.
- Timeboxing activity suppresses planning admonishments while active; idle threads flip to `unfinished` after 10 minutes.

## Reliability
- Avoid blocking Slack updates on long-running tools; keep processing messages concise.
- Prefer posting incremental updates (thread link + status) over silent waits.

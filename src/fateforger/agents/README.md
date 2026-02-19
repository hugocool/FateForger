# Agents

Top-level agent registry. Each subfolder owns one specialist agent (AutoGen `RoutedAgent` or `BaseChatAgent`) that the receptionist can hand off to.

## Agent Index

| Folder | Agent | Status | Purpose |
|--------|-------|--------|---------|
| `receptionist/` | Receptionist | Implemented | LLM-based triage: classifies user intent, routes to specialist via AutoGen handoff tools. |
| `timeboxing/` | PlanningCoordinator | Implemented, Tested | Stage-gated daily schedule builder with calendar sync. See `timeboxing/README.md`. |
| `admonisher/` | HauntRouter | Implemented | Accountability nudge system ("haunters"): persistent follow-ups on commitments via calendar MCP. |
| `schedular/` | SchedularAgent | Implemented | Calendar scheduling: slot finding, event upsert, plan-vs-calendar diffing via Google Calendar MCP. |
| `tasks/` | TaskMarshal | Implemented, Documented, Tested | Task capture, prioritization (now/next/later), TickTick list-management, and Notion sprint-domain operations (search/filter, relation linking, patch-style page edits). |
| `revisor/` | RevisorAgent | Implemented, Documented | Weekly retros and long-term project review; hands off operational sprint execution requests to `tasks_agent`. |
| `strategy/` | — | Roadmap | Placeholder for future strategy workflows. No code yet. |
| `task_marshal/` | — | Roadmap (stub) | Stub with bare imports only. May be superseded by `tasks/`. |

## Routing Flow

```
Slack message
  -> slack_bot/handlers.py
    -> Receptionist (intent classification)
      -> handoff to specialist agent
        -> specialist response
          -> Slack reply
```

The receptionist uses AutoGen handoff tools to delegate to the correct specialist. Each specialist owns its own session state and LLM interactions.

Routing note:
- Questions that check whether an event is already scheduled on the calendar
  (for example: "is a timeboxing session planned for tomorrow?") route to
  `planner_agent` as calendar inspection, even when the word "timeboxing" appears.

## Shared Patterns

- All agents use **AutoGen AgentChat** (`RoutedAgent`, `AssistantAgent`, `BaseChatAgent`).
- LLM provider: OpenRouter -> Gemini (see `src/fateforger/llm/` for client config).
- Structured output: use `output_content_type` for simple Pydantic models; use **schema-in-system-prompt** for models with `oneOf` / discriminated unions (see `timeboxing/AGENTS.md` for rationale).
- TOON tabular format for list-shaped LLM inputs (see `src/fateforger/llm/toon.py`).

## Subfolder Documentation

Each implemented agent folder should have:
- `README.md` — file index, architecture, status, test commands.
- `AGENTS.md` — operational rules for that agent subtree.

Currently documented:
- `timeboxing/`: full README + AGENTS.
- `admonisher/`: AGENTS.md with intent/handoff rules.
- `tasks/`: README + AGENTS (TickTick list-management tool contract).
- Others: minimal READMEs, no AGENTS.md.

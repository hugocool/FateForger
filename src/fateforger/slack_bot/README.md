# Slack Bot

Socket Mode Slack bot that routes user interactions to specialist agents. Built on Slack Bolt with AutoGen runtime bridging.

## Status

| Feature | Status |
|---------|--------|
| Socket Mode + Bolt listeners | Implemented |
| Timeboxing slash command + Stage 0 UI | Implemented |
| Thread-scoped focus routing | Implemented |
| Constraint review modals | Implemented |
| Planning/scheduling UI | Implemented, Tested (strict add-to-calendar success verification + explicit link surfacing) |
| Haunt delivery (nudges) | Implemented |
| Sync engine confirm/cancel/undo buttons | Implemented, Tested |
| Dispatch timeout fallback reply | Implemented, Tested |

## File Index

### Core

| File | Responsibility |
|------|---------------|
| `bot.py` | Application entry point: builds `AsyncApp` (Slack Bolt), initializes DB engine, AutoGen runtime, workspace store, and registers all handlers. |
| `bootstrap.py` | Startup provisioning: ensures Slack workspace channels, personas, and agent bindings exist (idempotent). |
| `handlers.py` | Central Slack event/action router (~2000 lines): registers all Bolt listeners (slash commands, message events, button actions, modal submissions) and dispatches to agents. |

### Agent Bridge

| File | Responsibility |
|------|---------------|
| `relay_agent.py` | AutoGen `RoutedAgent` that subscribes to agent-output topics and relays them to per-thread asyncio queues for the Bolt bridge to post. |
| `focus.py` | In-memory, TTL-backed mapping from Slack threads to the agent type that "owns" a conversation (thread-scoped focus routing). |
| `topics.py` | AutoGen topic definitions for agent-to-Slack message routing. |

### Timeboxing UI

| File | Responsibility |
|------|---------------|
| `timeboxing_commit.py` | Stage 0 "commit day" Slack UI: day picker + start button. Handles user day-selection before a timeboxing session begins. Action IDs: `FF_TIMEBOX_COMMIT_*`. |
| `timeboxing_submit.py` | Stage 5 submit/cancel/undo Slack action bridge. Parses button metadata and dispatches typed submit/undo messages to `timeboxing_agent`. |
| `constraint_review.py` | Block Kit modals and action payloads for reviewing/editing timeboxing constraints extracted from conversations. |

### Planning/Scheduling UI

| File | Responsibility |
|------|---------------|
| `planning.py` | Planning/scheduling Slack UI: renders suggested calendar slots, processes user date/time edits, upserts events via scheduling agent. Action IDs: `FF_EVENT_*`. |
| `planning_ids.py` | Generates deterministic, MCP-compatible Google Calendar event IDs (base32hex) for planning sessions. |

### Shared Utilities

| File | Responsibility |
|------|---------------|
| `messages.py` | Lightweight Slack-specific message payload dataclasses for passing Block Kit content between agents and the Slack layer. |
| `ui.py` | Reusable Block Kit helpers: link buttons, "open link" section blocks. |

### Workspace Management

| File | Responsibility |
|------|---------------|
| `workspace.py` | Domain model for a resolved Slack workspace: channel-to-agent mappings, persona definitions, global `WorkspaceConfig` singleton. |
| `workspace_store.py` | SQLModel-backed persistence for Slack channel-binding records (team/channel/agent mapping). |

## Interaction Model

### Timeboxing Flow

```
/timebox  ->  handlers.py (slash command)
  -> timeboxing_commit.py (Stage 0: day picker UI)
    -> user selects day + clicks Start
      -> handlers.py dispatches StartTimeboxing to PlanningCoordinator
        -> coordinator runs stage machine (Stages 1-5)
          -> relay_agent.py drains agent output queue
            -> Bolt posts messages to Slack thread
```

### Thread Focus

- Each Slack thread is "owned" by one agent type at a time (via `focus.py`).
- Subsequent messages in a thread route to the owning agent without re-triage.
- Focus expires on TTL or explicit release.

### Action Handler Registry

Button/action callbacks registered in `handlers.py`:

| Action ID prefix | Handler | Purpose |
|-----------------|---------|---------|
| `FF_TIMEBOX_COMMIT_*` | `timeboxing_commit.py` | Stage 0 day selection |
| `FF_EVENT_*` | `planning.py` | Calendar slot editing |
| `ff_constraint_*` | `constraint_review.py` | Constraint review modals |
| `ff_timebox_confirm_submit` | `timeboxing_submit.py` | Submit Stage 5 plan to calendar |
| `ff_timebox_cancel_submit` | `timeboxing_submit.py` | Cancel pending Stage 5 submit and return to refine |
| `ff_timebox_undo_submit` | `timeboxing_submit.py` | Undo latest Stage 5 submission |

## How to Run

```bash
# Start infra (MCP containers) for local debug
# Then run the bot from VS Code debugger (launch.json) or:
poetry run python -m fateforger.slack_bot.bot

# Docker (production)
docker compose up -d slack-bot calendar-mcp
```

# Slack Bot

Slack Socket Mode bot that routes user messages to specialist agents (timeboxing, planning, tasks, revisor).

## Timeboxing Entry Points

- Slash command `/timebox`: starts a timeboxing session by routing a synthetic message event through the normal Slack event router.
  - Implementation: `src/fateforger/slack_bot/handlers.py`
- Stage 0 “commit day” UI: user confirms which day to timebox before Stage 1 starts.
  - UI builder: `src/fateforger/slack_bot/timeboxing_commit.py`

## User Interaction Model

- A timeboxing session is anchored in a Slack thread inside the timeboxing channel.
- The bot can also use DMs as a control surface (links, nudges, and ephemeral responses).
- The coordinator posts stage updates and short background status notes while durable constraints/calendar data sync in the background.

## Reliability Notes

- Prefer non-blocking background work (MCP calls, durable writes) and keep Slack responses fast.
- If an LLM/tool call times out, the bot should surface a clear retry message rather than silently stalling.

See `AGENTS.md` in this folder for operational rules.

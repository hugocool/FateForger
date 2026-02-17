# Schedular Agent

## Status

- Planner diffing structured-output path uses a strict `list_events` tool wrapper.
- Tool payload parsing failures now raise explicit runtime errors (no silent fallback).

## Key Files

- `agent.py`: main planner routing and calendar upsert orchestration.
- `diffing_agent.py`: structured diff planning helper (`PlanDiff`) with calendar tool use.
- `messages.py`: typed message contracts.

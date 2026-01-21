# Timeboxing Agent

Stage-gated timeboxing workflow that builds daily schedules.

Key files:
- `agent.py`: orchestration and stage gating.
- `prompts.py`: system prompts and stage prompts.
- `stage_gating.py`: stage schema and decision logic.
- `preferences.py`: session constraint store.
- `notion_constraint_extractor.py`: durable constraint extraction.

See `AGENTS.md` in this folder for operational rules.

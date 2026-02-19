# Revisor Agent Notes

## Scope
- Revisor owns strategic weekly/monthly review guidance and long-term prioritization.

## Handoff Rules
- Keep strategy/retrospective reasoning in `revisor_agent`.
- For operational sprint execution requests (ticket finding/filtering, parent/subtask linking, patching Notion sprint page content), hand off to `tasks_agent`.
- Do not add deterministic keyword routing logic; rely on LLM handoff behavior.

---
paths:
  - "src/fateforger/agents/tasks/*.py"
  - "src/fateforger/agents/tasks/**/*.py"
---

# Tasks agent

- Keep TickTick MCP IO in `list_tools.py` and sprint MCP IO in `notion_sprint_tools.py`; do not spread MCP call choreography into Slack handlers.
- If list or item resolution is ambiguous, return structured ambiguity and ask a focused follow-up. Never perform destructive operations while ambiguity exists.
- Default to dry-run previews for sprint page edits; fail safely on ambiguous or conflicting matches.
- Proposal-card interactions follow the slack_bot proposal contract: NL and UI converge on one typed request/update path.
- **Deterministic matching is allowed for slash commands only** — `/task-refine` is system-minted. Start and cancel phrases in English are a judgement and go to the model (ruling C4; code fix #454).
- Guided refinement advances a phase only when `gate_met=true`; otherwise hold the phase and request the missing fields.
- Tests: start/cancel commands, gate not met (phase held), gate met (phase advance), close-recap persistence.

# Admonisher Agent Notes

## Intent & Handoffs
- Intent classification must use LLMs (AutoGen handoff tools); do not use regex or keyword checks.
- If the user asks for timeboxing or a concrete daily plan, call the `timeboxing_agent` handoff tool immediately.
- If the user asks to schedule or change calendar events, hand off to `planner_agent`.

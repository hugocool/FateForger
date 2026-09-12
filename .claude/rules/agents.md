---
paths:
  - "src/fateforger/agents/*.py"
  - "src/fateforger/agents/**/*.py"
---

# Agents — cross-cutting

- Intent classification is the receptionist's, via LLM handoff tools. **Never** add regex/keyword-based intent routing; use LLM classification or explicit slash commands.
- Each specialist agent declares a clear `description` string for the receptionist's handoff tool.
- Specialist agents extend `RoutedAgent` (handoff-capable) or `BaseChatAgent` (custom lifecycle); register tools via `FunctionTool`, and keep tool IO in the coordinator, not in stage nodes.
- Use `output_content_type=MyModel` only when the Pydantic model has no `oneOf` / discriminated union — see the tmbx rule for what breaks when it does.
- A proposed domain object that can be confirmed, edited or submitted from Slack follows one typed contract end to end; NL and UI confirmations must not diverge downstream.

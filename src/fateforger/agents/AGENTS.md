# Agents — Agent Notes

**Scope:** Cross-cutting rules for all agent subfolders. For the agent index and routing flow, see `README.md` in this folder. For agent-specific rules, check each subfolder's `AGENTS.md`.

## AutoGen Conventions

- All specialist agents extend `RoutedAgent` (for handoff-capable agents) or `BaseChatAgent` (for custom lifecycle).
- Use `AssistantAgent` for single-turn LLM calls within agent implementations.
- Register tools via `FunctionTool`; tool IO stays in the coordinator/agent, not in stage nodes.

## Structured Output

- Use `output_content_type=MyModel` when the Pydantic model has no `oneOf` / discriminated unions and the model client supports structured output.
- For models with `oneOf` (e.g., `TBPatch`, `TBOp`): inject `Model.model_json_schema()` into the system prompt and parse raw JSON text. See `timeboxing/patching.py` for the canonical example.
- Wrap structured agents in an unpacking pattern when downstream consumers expect `TextMessage`. See the `UnpackingAgent` pattern below.

### UnpackingAgent Pattern

When an inner agent uses `output_content_type` but the outer flow needs `TextMessage`:

1. Create a `BaseChatAgent` wrapper.
2. Run the inner `AssistantAgent` (structured output).
3. Read `StructuredMessage.content` (Pydantic object).
4. Emit `TextMessage` derived from `model.unpack()` or equivalent.

This keeps the schema lock on the backend while producing plain text for Slack/UI.

## Intent Classification

- The receptionist handles intent classification via LLM handoff tools.
- **Never** add regex/keyword-based intent routing. Use LLM classification or explicit slash commands.
- Each specialist agent should declare a clear `description` string for the receptionist's handoff tool.

## Adding a New Agent

1. Create a new subfolder under `agents/`.
2. Implement the agent extending `RoutedAgent` or `BaseChatAgent`.
3. Register the handoff in `receptionist/agent.py`.
4. Add a `README.md` (file index + purpose + status) and `AGENTS.md` (operational rules).
5. Update this folder's `README.md` agent index table.
6. Add the Slack routing in `slack_bot/handlers.py` if the agent needs direct Slack triggers.

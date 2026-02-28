---
applyTo: "**"
---
read agents.md!!
# 🧠 MemoriPilot Memory-First Directive
**Always call `memory_bank_show_memory` before you answer or run code.**  
Memory is the single source of truth for project knowledge and history.

## Poetry-First Development Environment

**CRITICAL**: This project uses Poetry for ALL Python operations. Never use pip directly.

```bash
# Run any script, test, or command
poetry run python script_name.py
poetry run pytest tests/
poetry run python -m productivity_bot.planner_bot

# Install dependencies
poetry add package_name              # Add runtime dependency
poetry add --group dev package_name  # Add dev dependency
```


## When new knowledge appears
| Situation | Call this MemoriPilot tool |
|-----------|---------------------------|
| Architectural / tech choice | `memory_bank_log_decision` |
| Switch of focus / task | `memory_bank_update_context` |
| Progress update (done/doing/next) | `memory_bank_update_progress` |
| New pattern / convention | `memory_bank_update_system_patterns` |

## Working-mode hints
- **architect** for high-level design  
- **code** for implementation details  
- **debug** for troubleshooting  
- **ask** for information retrieval  
Use `memory_bank_switch_mode` when mode changes.

> Detailed architecture, workflows, commands and patterns live in the **memory-bank/** directory and must be consulted via `memory_bank_show_memory`.





## AI-Specific Guidelines

### LLM Message Generation
**NEVER use pre-written templates**. All user-facing messages must be LLM-generated with:
- Varied system prompts for different haunter personalities
- Context-aware responses based on attempt counts
- Natural language time parsing capabilities

### Agent Handoff Pattern
```python
# Structured handoff to planning agents
payload = HauntPayload(session_id=UUID, action="action_type", ...)
router = RouterAgent()
await router.route_payload(payload)
```

### Database Integration
- Use `mapped_column(JSON, default=list)` for flexible data
- Async session management with proper context cleanup
- Migration-friendly schema evolution

## Critical Dependencies

- **slack-bolt**: Async Slack app framework
- **autogen-agentchat**: AI planning agents  
- **apscheduler**: Background job persistence
- **sqlalchemy[asyncio]**: Async ORM with JSON columns
- **pydantic-settings**: Environment-based configuration

## Debugging Tips

- Check APScheduler job table for persistent scheduling issues
- Use `poetry run python validate_*.py` for structural validation
- Slack threading issues: verify `thread_ts` persistence
- LLM integration: check OpenAI API key configuration and system prompts

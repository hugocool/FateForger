# FateForger AI Development Guide

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

## Architecture Overview

FateForger is an **AI-powered productivity system** with a modular haunter architecture:

### Core Components
- **Haunter System**: Three-stage lifecycle (Bootstrap → Commitment → Incomplete)
- **MCP Integration**: Model Context Protocol for calendar operations 
- **AutoGen AI**: LLM-powered planning with structured outputs
- **APScheduler**: Persistent job scheduling with SQLAlchemy store
- **Slack Integration**: Modal interfaces with threaded conversations

### Haunter Lifecycle Pattern
```
Bootstrap → Commitment → Incomplete
  ↓           ↓            ↓
Daily      Event-Start   Overdue
Check      Haunting      Polling
```

## Key File Patterns

### 1. Haunter Architecture (`src/productivity_bot/haunting/`)
```
haunting/
├── base_haunter.py          # Abstract base with exponential backoff
├── bootstrap/haunter.py     # Daily planning outreach
├── commitment/haunter.py    # Event-start engagement  
├── incomplete/haunter.py    # Overdue session follow-up
└── */action.py             # Pydantic schemas per haunter type
```

**Pattern**: Each haunter inherits from `BaseHaunter` with specific `backoff_base_minutes`:
- Bootstrap: 15min (moderate persistence)
- Commitment: 10min (urgent follow-up)
- Incomplete: 20min (gentle encouragement)

### 2. LLM Integration Pattern
All haunters use OpenAI AsyncClient with structured outputs:
```python
async def parse_intent(self, text: str) -> ActionSchema:
    client = AsyncOpenAI(api_key=config.openai_api_key)
    # Uses haunter-specific system prompts for varied messages
```

### 3. Database Models (`src/productivity_bot/models.py`)
- `PlanningSession` with `slack_sched_ids: List[str]` for message tracking
- `PlanningBootstrapSession` for modular commitment types
- Async SQLAlchemy with `get_db_session()` context manager

## Development Workflows

### Validation Commands (Always via Poetry)
```bash
# Ticket validation (project-specific pattern)
make validate-ticket5-structure    # Structure validation
make validate-ticket5             # Complete validation
poetry run python validate_ticket5_structure.py

# Testing
poetry run pytest tests/
poetry run python test_ticket4_integration.py
```

### Slack Development
- Use `slack_utils.py` functions: `schedule_dm()`, `delete_scheduled()`
- All Slack operations track IDs in `slack_sched_ids` column
- Thread-aware messaging with `thread_ts` parameter

### Scheduler Operations
```python
from .scheduler import get_scheduler, schedule_event_haunt
scheduler = get_scheduler()  # Singleton with SQLAlchemy persistence
```

## Project-Specific Conventions

### 1. Import Patterns
```python
# Lazy imports to avoid circular dependencies
from productivity_bot.common import get_config, get_logger
from productivity_bot.database import get_db_session
```

### 2. Error Handling
- Comprehensive logging with haunter-specific loggers
- Graceful degradation with TODO stubs for complex implementations
- Database async patterns with proper session management

### 3. Configuration
- Environment-based config via Pydantic Settings
- MCP endpoint integration: `http://mcp:4000`
- Timezone-aware scheduling: Amsterdam time for daily operations

## Testing Patterns

### Structure Validation
The project uses custom validation scripts that check for method existence:
```python
def check_code_contains(file_path: str, search_text: str, description: str) -> bool:
    # Validates implementation completeness without import issues
```

### Integration Testing
- Mock Slack/Scheduler dependencies
- AsyncMock for async operations
- UUID-based session tracking

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

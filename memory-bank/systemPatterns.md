# System Patterns

## Architectural Patterns

- Pattern 1: Description

## Design Patterns

- Pattern 1: Description

## Common Idioms

- Idiom 1: Description

## AutoGen MCP Integration Pattern

Use AutoGen's built-in MCP system for calendar operations. NO manual HTTP calls, NO direct REST API usage. AutoGen AssistantAgent with proper MCP server configuration handles all protocol communication automatically.

### Examples

- real_calendar_agent = AssistantAgent with MCP tools
- Use AutoGen's on_messages() with CancellationToken
- Configure MCP server endpoint in AutoGen agent setup

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
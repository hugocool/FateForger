
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

# FateForger

FateForger is an AI-powered productivity system that uses AutoGen agents to manage calendar events, scheduling, and task planning through Slack integration.

## Architecture Overview

The system is built using AutoGen's agent framework with MCP (Model Context Protocol) integration for Google Calendar access. It follows a modular architecture with specialized agents handling different aspects of productivity management.

## Directory Structure

```
fateforger/
├── readme.md                    # This file - project documentation
├── core/                        # Core system components
│   ├── __init__.py
│   ├── README.md               # Core module documentation
│   ├── bootstrap.py            # Application initialization (empty)
│   ├── config.py               # Pydantic settings configuration
│   ├── logging.py              # Logging utilities
│   └── runtime.py              # AutoGen runtime initialization
├── agents/                      # Specialized AI agents
│   ├── __init__.py
│   ├── README.md               # Agents module documentation
│   ├── admonisher/             # Task accountability and reminder agent
│   │   ├── __init__.py
│   │   ├── README.md
│   │   ├── base.py             # Base haunter class with Slack/scheduler utilities
│   │   ├── bootstrap.py        # Agent initialization
│   │   ├── calendar.py         # Calendar-based reminders
│   │   ├── commitment.py       # Commitment tracking
│   │   ├── incomplete.py       # Incomplete task handling
│   │   └── models.py           # SQLAlchemy models (PlanningSession, SlackMessage)
│   ├── revisor/                # Task revision and optimization agent (empty)
│   ├── schedular/              # Calendar planning and scheduling agent
│   │   ├── __init__.py
│   │   ├── readme.md           # Scheduler agent documentation (empty)
│   │   ├── agent.py            # Main PlannerAgent implementation
│   │   ├── diffing_agent.py    # Calendar diff analysis
│   │   └── models/             # Data models for scheduling
│   │       ├── __init__.py
│   │       ├── calendar_contract.py    # Calendar API contracts
│   │       └── calendar_event.py       # CalendarEvent Pydantic models
│   └── task_marshal/           # Task coordination and routing agent
│       ├── __init__.py
│       ├── readme.md           # Task marshal documentation (empty)
│       └── agent.py            # Task coordination logic
└── tools/                      # MCP and external tool integrations
    ├── __init__.py
    └── calendar_mcp.py         # Google Calendar MCP server configuration
```

## Core Components

### `core/`
Contains fundamental system components:

- **`config.py`**: Environment-based configuration using Pydantic settings
  - Slack bot tokens and signing secrets
  - OpenAI API key configuration
  - Database URL and MCP server parameters
  - Calendar webhook and scheduler settings

- **`runtime.py`**: AutoGen agent runtime initialization
  - Registers all agents with the SingleThreadedAgentRuntime
  - Sets up message routing and agent communication

- **`logging.py`**: Centralized logging utilities
  - Provides `get_logger()` function for consistent logging across modules

### `agents/`
Specialized AI agents for different productivity tasks:

#### `admonisher/`
Accountability and reminder system:
- **`base.py`**: Abstract base class for haunter agents with Slack and scheduler utilities
- **`models.py`**: Database models for planning sessions and Slack message tracking
- **Calendar, commitment, and incomplete task modules** for different reminder strategies

#### `schedular/`
Calendar planning and event management:
- **`agent.py`**: Main PlannerAgent using AutoGen's AssistantAgent
- **`diffing_agent.py`**: Analyzes calendar changes and conflicts
- **`models/calendar_event.py`**: Comprehensive CalendarEvent Pydantic models matching Google Calendar API v3
- **`models/calendar_contract.py`**: API contract definitions

#### `task_marshal/`
Task coordination and routing:
- **`agent.py`**: Coordinates between different agents and manages task flow

#### `revisor/` 
Task revision and optimization (currently empty - planned for future development)

### `tools/`
External integrations and MCP tools:

- **`calendar_mcp.py`**: Google Calendar MCP server integration
  - Provides `get_calendar_mcp_tools()` function for AutoGen agents
  - Uses HTTP transport for MCP server communication
  - Configurable timeout and server URL parameters

## Key Features

### AutoGen Integration
- Uses AutoGen's `AssistantAgent` for LLM-powered agents
- Implements `RoutedAgent` for message handling and routing
- Integrates with AutoGen's MCP system for external tool access

### Calendar Management
- Full Google Calendar API v3 compatibility through MCP
- Supports complex event structures with nested Pydantic models
- Calendar event CRUD operations and planning workflows

### Slack Integration
- Bot token and webhook support
- Message scheduling and reminder system
- Thread-aware conversation handling

### Database Persistence
- SQLAlchemy async ORM with JSON column support
- Pydantic model serialization to database
- Planning session and message tracking

## Development Status

This is an active development project with the following architectural decisions:
- ✅ Uses AutoGen AssistantAgent (not custom classes)
- ✅ Uses AutoGen's MCP integration (not manual HTTP calls)
- ✅ Connects to real calendar data (not mock data)
- ✅ All operations have timeouts to prevent hanging

## Configuration

The system uses environment variables for configuration (see `core/config.py`):
- Slack tokens and secrets
- OpenAI API key
- Database connection strings
- MCP server parameters
- Calendar webhook settings

## Getting Started

1. Configure environment variables in `.env` file
2. Initialize the database using provided migration scripts
3. Start the MCP server for calendar integration
4. Run the application using Poetry: `poetry run python -m fateforger`
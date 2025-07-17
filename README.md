# FateForger

AI-powered productivity system that **forges your fate** through intelligent daily planning, persistent reminders, and seamless calendar integration. Unlike traditional productivity tools that passively wait for you to open them, FateForger actively shapes your destiny by ensuring you maintain your planning rituals and follow through on commitments.

## Features

### 🤖 AutoGen AI Planning
- **Intelligent Schedule Optimization**: AI-powered daily plan generation using AutoGen
- **Calendar Integration**: Seamless integration with Google Calendar via MCP (Model Context Protocol)
- **Context-Aware Suggestions**: Smart recommendations based on existing calendar events and availability
- **Time-Boxing Automation**: Automated time-block suggestions for goals and tasks

### 📋 Interactive Planning
- **Slack Modal Interface**: User-friendly planning forms within Slack
- **Goal Setting & Tracking**: Set and track daily goals with AI enhancement
- **Session Persistence**: All planning sessions stored and retrievable
- **Real-time Collaboration**: Team-based planning and coordination

### 👻 Smart Reminders (Haunter System)
- **Exponential Back-off**: Intelligent reminder scheduling (5→10→20→40→60 min intervals)
- **Persistent Follow-ups**: Automated haunting until tasks are completed
- **Status-Aware Notifications**: Contextual reminders based on planning session status
- **Slack Integration**: Native Slack notifications with interactive buttons

### 📅 Calendar Management
- **MCP Server Integration**: Modern calendar operations via Model Context Protocol
- **Availability Analysis**: Real-time calendar conflict detection
- **Event Management**: Create, update, and manage calendar events
- **Multi-calendar Support**: Work with multiple Google Calendar accounts

## Quick Start

1. **Setup Environment**

   ```bash
   cp .env.template .env
   # Edit .env with your Slack tokens, OpenAI API key, and MCP endpoint
   ```

2. **Install Dependencies with Poetry**

   ```bash
   poetry install
   ```

3. **Development Commands (Always use Poetry)**

   ```bash
   # Run any Python script
   poetry run python script_name.py
   
   # Run tests
   poetry run pytest
   
   # Validate Ticket 4 implementation
   make validate-all
   
   # Or manually:
   poetry run python validate_syntax_ticket4.py
   poetry run python test_ticket4_integration.py
   ```

4. **Development with Docker (Alternative)**

   ```bash
   ./setup-dev.sh
   docker-compose run --rm dev
   ```

5. **Run All Services**

   ```bash
   ./run.sh  # Starts all bots, AI agents, and servers with ngrok tunnels
   ```

## Project Structure

```text
productivity_bot/
├── src/productivity_bot/      ← Main Python package
│   ├── __init__.py
│   ├── common.py             ← Shared utilities & MCP integration
│   ├── planner_bot.py        ← Task planning bot with AutoGen
│   ├── autogen_planner.py    ← AutoGen AI agent & MCP tools
│   ├── haunter_bot.py        ← Reminder/follow-up bot
│   ├── scheduler.py          ← APScheduler integration
│   ├── models.py             ← Database models
│   └── calendar_watch_server.py ← Calendar webhook server
├── tests/                    ← Comprehensive test suite
│   ├── test_autogen_planner.py ← AutoGen functionality tests
│   ├── test_haunter.py       ← Haunter system tests
│   └── test_planner_bot.py   ← Planning bot integration tests
├── docs/                     ← Full documentation
│   ├── api/autogen_planner.md ← AutoGen API documentation
│   └── architecture/         ← System design docs
├── .env.template            ← Environment configuration template
└── run.sh                   ← Launch script for all services
```

## Development

For detailed development setup instructions, see [DEVELOPMENT.md](./DEVELOPMENT.md).

## Services

### Core Components
- **Planner Bot**: AI-enhanced task planning and scheduling with Slack integration
- **AutoGen Agent**: Intelligent daily plan generation and optimization
- **Haunter Bot**: Persistent reminder system with exponential back-off
- **Calendar Watch Server**: Real-time calendar webhook processing

### AI & Integration
- **MCP Server**: Model Context Protocol server for calendar operations
- **APScheduler**: Background job scheduling for reminders and follow-ups
- **Database Layer**: PostgreSQL/SQLite with SQLAlchemy ORM
- **Slack Integration**: Native Slack bot with modals, buttons, and threading

## Environment Configuration

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
SLACK_SIGNING_SECRET=your-signing-secret

# AI Configuration
OPENAI_API_KEY=your-openai-api-key

# MCP Integration
MCP_ENDPOINT=http://mcp:4000

# Database
DATABASE_URL=sqlite+aiosqlite:///data/fateforger.db

# Development
LOG_LEVEL=INFO
```

## Usage Examples

### Start a Planning Session
```
/plan_today
```
Opens an interactive Slack modal for daily planning with AI enhancement.

### AI-Enhanced Planning Flow
1. User fills out planning modal with goals
2. AutoGen agent analyzes calendar and generates optimized schedule
3. AI suggestions sent as follow-up message with action buttons
4. Haunter system schedules persistent reminders
5. Exponential back-off ensures follow-through

### Manual Reminders
```
/haunt "Complete project review" 2h
```
Sets up persistent haunting with smart back-off intervals.

## API Documentation

Comprehensive API documentation available in the `docs/` directory:

- [AutoGen Planner API](./docs/api/autogen_planner.md)
- [Haunter Bot API](./docs/api/haunter_bot.md)
- [Planner Bot API](./docs/api/planner_bot.md)
- [Architecture Overview](./docs/architecture/overview.md)

## Testing

```bash
# Run all tests
pytest

# Test specific components
pytest tests/test_autogen_planner.py -v
pytest tests/test_haunter.py -v

# Run with coverage
pytest --cov=src/productivity_bot --cov-report=html
```

## Architecture

The system follows a microservices architecture with:

- **Event-Driven Design**: Slack events trigger planning and reminder workflows
- **AI-First Approach**: AutoGen agents enhance every planning interaction
- **Persistent State**: Database-backed session and reminder tracking
- **Modern Protocols**: MCP for calendar integration, APScheduler for job management
- **Fault Tolerance**: Comprehensive error handling and retry mechanisms

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add comprehensive tests and documentation
4. Ensure all linting and type checking passes
5. Submit a pull request

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

## License

MIT License - see [LICENSE](./LICENSE) for details.

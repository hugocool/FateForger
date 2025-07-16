# admonish

productivity bot

## Quick Start

1. **Setup Environment**

   ```bash
   cp .env.template .env
   # Edit .env with your Slack tokens and other configuration
   ```

2. **Development with Docker (Recommended)**

   ```bash
   ./setup-dev.sh
   docker-compose run --rm dev
   ```

3. **Run All Services**

   ```bash
   ./run.sh  # Starts all bots and servers with ngrok tunnels
   ```

## Project Structure

```text
productivity_bot/
├── src/productivity_bot/      ← Main Python package
│   ├── __init__.py
│   ├── common.py             ← Shared utilities
│   ├── planner_bot.py        ← Task planning bot
│   ├── haunter_bot.py        ← Reminder/follow-up bot
│   └── calendar_watch_server.py ← Calendar webhook server
├── tests/                    ← Test suite
├── .env.template            ← Environment configuration template
└── run.sh                   ← Launch script for all services
```

## Development

For detailed development setup instructions, see [DEVELOPMENT.md](./DEVELOPMENT.md).

## Services

- **Planner Bot**: Helps with task planning and scheduling
- **Haunter Bot**: Sends reminders and follows up on tasks  
- **Calendar Watch Server**: Receives calendar webhooks and triggers actions

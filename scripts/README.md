# Scripts Directory

This directory contains utility and setup scripts for the Admonish productivity bot.

## Available Poetry Commands

### Main Application Scripts
- `poetry run plan` - Start the planner bot
- `poetry run haunt` - Start the haunter bot  
- `poetry run watch` - Start the calendar watch server

### Docker & Infrastructure Management
- `poetry run mcp-build` - Build the MCP server Docker image
- `poetry run mcp-start` - Start the MCP server container
- `poetry run mcp-stop` - Stop and remove the MCP server container
- `poetry run mcp-logs` - Follow MCP server logs
- `poetry run infra-up` - Start all services with docker-compose
- `poetry run infra-down` - Stop all services with docker-compose

### Development Environment
- `poetry run dev-setup` - Complete development environment setup
  - Creates .env from .env.example if needed
  - Initializes database
  - Builds and starts MCP server
  - Runs database migrations

## Quick Start

1. **Initial Setup:**
   ```bash
   poetry install
   poetry run dev-setup
   ```

2. **Daily Development:**
   ```bash
   # Start MCP server
   poetry run mcp-start
   
   # Start main services
   poetry run watch    # Calendar watch server
   poetry run haunt    # Haunter bot
   
   # Check logs
   poetry run mcp-logs
   ```

3. **Managing Services:**
   ```bash
   # Start all infrastructure
   poetry run infra-up
   
   # Stop everything
   poetry run infra-down
   ```

## Files

| File | Purpose |
|------|---------|
| `init_db.py` | Initialize the database schema and create tables. |
| `setup_test_db.py` | Set up test database configuration. |
| `docker_utils.py` | Docker container management utilities. |
| `dev_utils.py` | Development environment setup and maintenance. |
| `constraint_mcp_server.py` | Constraint-memory MCP server (wraps Notion access for durable timeboxing preferences). |
| `seed_constraint_types.py` | Seeds constraint type definitions into the database. |
| `timebox_patch_demo.py` | Demo script for timebox patching flow. |
| `auth_calendar.sh` | Google Calendar OAuth authentication helper. |

### `dev/` — Development and Debugging Scripts

| File | Purpose |
|------|---------|
| `verify_stack.py` | Verifies the Docker Compose stack is healthy. |
| `slack_bot_dev.py` | Local Slack bot dev runner. |
| `debug_calendar_mcp.py` | Debug Calendar MCP connectivity. |
| `check_anchor_event.py` | Check anchor event status. |
| `check_planning_anchors.py` | Check planning anchor events. |
| `check_planning_event.py` | Check specific planning event. |
| `force_nudge.py` | Force-trigger a planning nudge. |
| `test_reconcile_logic.py` | Test planning reconciliation logic. |
| `notebook_workflow_checks.py` | Validates notebook metadata/lifecycle policy and optional DONE clean-kernel execution checks. |

### `notion/` — Notion Integration Scripts

| File | Purpose |
|------|---------|
| `create_sprint_ticket.py` | Create a sprint ticket in Notion. |

## Usage

Run scripts from the project root directory:

```bash
# Initialize database
poetry run python scripts/init_db.py

# Setup test database  
poetry run python scripts/setup_test_db.py
```

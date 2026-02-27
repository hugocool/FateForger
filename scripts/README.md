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
| `slack_user_timeboxing_driver.py` | Interactive driver that posts as a real Slack user token (`xoxp`) to exercise the true Slack inbound path in a timeboxing thread. |
| `slack_mcp_client.sh` | Starts/verifies/stops `tuannvm/slack-mcp-client` via Docker Compose using `.env` tokens with retry + healthcheck cycle. |
| `timebox_log_query.py` | Query indexed timeboxing/patcher/LLM-audit logs (`sessions`, `events`, `patcher`, `llm`) without manual grep. |
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
# Start + verify + stop slack-mcp-client in one run
scripts/dev/slack_mcp_client.sh cycle

# Keep it running
scripts/dev/slack_mcp_client.sh up

# Verify health (default checks http://localhost:38180/metrics)
scripts/dev/slack_mcp_client.sh verify

# Tear down
scripts/dev/slack_mcp_client.sh down

# Interactive timeboxing thread driver as a user (xoxp)
# Required scopes for the user token:
#   chat:write
#   channels:history (public channels)
#   groups:history (private channels)
#   im:history / mpim:history (DMs/MPDMs as needed)
SLACK_USER_TOKEN=xoxp-... \
.venv/bin/python scripts/dev/slack_user_timeboxing_driver.py \
  --channel C0AA6HC1RJL \
  --text "Start timeboxing for today."

# Attach to an existing thread and continue chatting
SLACK_USER_TOKEN=xoxp-... \
.venv/bin/python scripts/dev/slack_user_timeboxing_driver.py \
  --channel C0AA6HC1RJL \
  --thread-ts 1772194594.823669

# List newest indexed timeboxing session logs
.venv/bin/python scripts/dev/timebox_log_query.py sessions --limit 5

# Query latest events for a given session key
.venv/bin/python scripts/dev/timebox_log_query.py events \
  --session-key 1772229704.048009 \
  --limit 50

# Show only submit outcomes in that session
.venv/bin/python scripts/dev/timebox_log_query.py events \
  --session-key 1772229704.048009 \
  --event submission_result \
  --limit 10

# List newest indexed patcher log files
.venv/bin/python scripts/dev/timebox_log_query.py patcher --limit 5

# Query indexed LLM I/O audit records for a Slack thread/session
.venv/bin/python scripts/dev/timebox_log_query.py llm \
  --thread-ts 1772229704.048009 \
  --limit 50

# Optional override if your upstream MCP endpoint differs
SLACK_MCP_UPSTREAM_URL=http://host.docker.internal:3001/mcp scripts/dev/slack_mcp_client.sh cycle

# Initialize database
poetry run python scripts/init_db.py

# Setup test database  
poetry run python scripts/setup_test_db.py
```

## Observability Runbook (Prometheus + Grafana + MCP)

Bring up standalone local observability:

```bash
docker compose -f observability/docker-compose.yml up -d
```

Verify:

1. Prometheus targets: http://localhost:9090/targets
2. Grafana: http://localhost:3000 (`admin` / `admin`)
3. FateForger metrics endpoint: `http://localhost:9464/metrics` (when app runs with `OBS_PROMETHEUS_ENABLED=1`)

Prometheus MCP server (Codex local config):

- Server image: `ghcr.io/pab1it0/prometheus-mcp-server:latest`
- URL: `PROMETHEUS_URL=http://host.docker.internal:9090`
- Enabled tools only:
  - `health_check`
  - `execute_query`
  - `execute_range_query`
  - `list_metrics`
  - `get_metric_metadata`
  - `get_targets`

Combined Slack audit loop:

1. Drive a real Slack thread (`slack_user_timeboxing_driver.py` or Slack MCP client).
2. Detect anomalies in Prometheus (`fateforger_llm_calls_total`, `fateforger_llm_tokens_total`, `fateforger_tool_calls_total`, `fateforger_errors_total`, `fateforger_stage_duration_seconds_*`).
3. Correlate with indexed logs:
   - `timebox_log_query.py sessions/events`
   - `timebox_log_query.py llm`
4. Use `session_key`/`thread_ts`/`call_label` to isolate root cause and patch with tests first.

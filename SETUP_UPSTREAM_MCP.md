# Setup Instructions

This document describes how to set up the Admonish productivity bot with the new upstream MCP server configuration.

## Overview

The project now uses the upstream Google Calendar MCP server directly from the official repository, eliminating the need to maintain a local Dockerfile and ensuring you always get the latest tested configuration.

## Prerequisites

1. **Docker and Docker Compose** - Required for running the MCP server
2. **Poetry** - For Python dependency management
3. **Google Cloud Project** - With Calendar API enabled
4. **OAuth 2.0 Credentials** - Desktop application type

## Quick Start

### 1. Google Cloud Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
4. Create OAuth 2.0 credentials:
   - Go to **Credentials** → **Create Credentials** → **OAuth client ID**
   - Choose **Desktop app** as the application type
   - Download the credentials JSON file

### 2. Project Setup

1. **Copy OAuth credentials:**
   ```bash
   cp /path/to/your/downloaded/credentials.json secrets/gcal-oauth.json
   ```

2. **Configure environment:**
   Edit `.env` file and update:
   ```bash
   # Required: Add your API keys
   OPENAI_API_KEY=your_openai_api_key_here
   SLACK_BOT_TOKEN=your_slack_bot_token_here
   SLACK_APP_TOKEN=your_slack_app_token_here
   SLACK_SIGNING_SECRET=your_slack_signing_secret_here
   ```

3. **Install dependencies:**
   ```bash
   poetry install
   ```

4. **Set up development environment:**
   ```bash
   poetry run dev-setup
   ```

### 3. Running the Services

**Start all services:**
```bash
poetry run infra-up
```

**Or start individual services:**
```bash
# Start MCP server only
poetry run mcp-start

# Start the bot services
poetry run watch    # Calendar watch server
poetry run haunt    # Haunter bot
```

**Check logs:**
```bash
poetry run mcp-logs
```

**Stop services:**
```bash
poetry run infra-down
```

## Available Poetry Commands

### Main Application Scripts
- `poetry run plan` - Start the planner bot
- `poetry run haunt` - Start the haunter bot  
- `poetry run watch` - Start the calendar watch server

### Docker & Infrastructure Management
- `poetry run mcp-build` - Build the MCP server from upstream
- `poetry run mcp-start` - Start the MCP server container
- `poetry run mcp-stop` - Stop the MCP server container
- `poetry run mcp-logs` - Follow MCP server logs
- `poetry run infra-up` - Start all services with docker-compose
- `poetry run infra-down` - Stop all services with docker-compose

### Development Environment
- `poetry run dev-setup` - Complete development environment setup

## Architecture

### MCP Server (Port 3000)
- **Service:** `calendar-mcp`
- **Image:** Built directly from `https://github.com/nspady/google-calendar-mcp.git#v1.4.8`
- **Health Check:** `http://localhost:3000/healthz`
- **Transport:** HTTP mode
- **Volumes:** 
  - OAuth credentials: `./secrets/gcal-oauth.json` → `/app/gcp-oauth.keys.json`
  - Token storage: `calendar-mcp-tokens` → `/home/node/.config/google-calendar-mcp`

### Bot Services (Port 8000)
- **Service:** `bot`
- **MCP Endpoint:** `http://calendar-mcp:3000`
- **Health Check:** `http://localhost:8000/health`

### Development Service (Port 8001)
- **Service:** `dev`
- **Purpose:** Live reloading for development
- **Volumes:** Source code mounted for hot reloading

## Configuration

### Environment Variables

The `.env` file contains:

```bash
# MCP Server Configuration
MCP_VERSION=v1.4.8
GOOGLE_OAUTH_CREDENTIALS=/run/secrets/gcal-oauth.json
TRANSPORT=http
PORT=3000

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./data/admonish.db

# API Keys (required)
OPENAI_API_KEY=your_openai_api_key_here
SLACK_BOT_TOKEN=your_slack_bot_token_here
SLACK_APP_TOKEN=your_slack_app_token_here
SLACK_SIGNING_SECRET=your_slack_signing_secret_here

# Calendar Configuration
CALENDAR_WEBHOOK_SECRET=your_webhook_secret_here

# Other settings
SCHEDULER_TIMEZONE=UTC
DEBUG=true
LOG_LEVEL=INFO
```

### OAuth Setup

1. Place your Google OAuth credentials in `secrets/gcal-oauth.json`
2. The format should match `secrets/gcal-oauth.json.example`
3. Ensure it's a **Desktop application** type credential

## First Run

On first startup, the MCP server will:
1. Prompt for authentication in your browser
2. Generate and store access tokens
3. Be ready to handle calendar operations

The tokens are persisted in the `calendar-mcp-tokens` Docker volume.

## Upgrading MCP Version

To upgrade to a newer version of the Google Calendar MCP server:

1. Update `MCP_VERSION` in `.env`:
   ```bash
   MCP_VERSION=v1.5.0  # or latest version
   ```

2. Rebuild and restart:
   ```bash
   poetry run infra-down
   poetry run mcp-build
   poetry run infra-up
   ```

## Troubleshooting

### MCP Server Issues
```bash
# Check if MCP server is running
poetry run mcp-logs

# Restart MCP server
poetry run mcp-stop
poetry run mcp-start
```

### Authentication Issues
1. Verify your OAuth credentials are in the correct location
2. Check that your email is added as a test user in Google Cloud Console
3. Delete tokens and re-authenticate:
   ```bash
   docker volume rm infra_calendar-mcp-tokens
   poetry run mcp-start
   ```

### Port Conflicts
- MCP server: Port 3000
- Bot service: Port 8000  
- Dev service: Port 8001

Make sure these ports are available or update the `.env` file accordingly.

## Development

For local development with live reloading:

1. Start the dev service:
   ```bash
   poetry run infra-up dev
   ```

2. The dev container has your source code mounted and bash access for debugging.

## Benefits of This Setup

✅ **Always Up-to-Date:** Uses the official upstream Dockerfile  
✅ **Immutable Builds:** Pinned to specific version tags  
✅ **No Maintenance:** No need to maintain local Dockerfile  
✅ **Tested Configuration:** Uses the author's recommended setup  
✅ **Easy Upgrades:** Just bump the version tag  
✅ **Full Integration:** Proper token persistence and health checks

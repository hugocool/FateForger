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

- `init_db.py` - Initialize the database schema and create tables
- `setup_test_db.py` - Set up test database configuration
- `docker_utils.py` - Docker container management utilities
- `dev_utils.py` - Development environment setup and maintenance

## Usage

Run scripts from the project root directory:

```bash
# Initialize database
poetry run python scripts/init_db.py

# Setup test database  
poetry run python scripts/setup_test_db.py
```

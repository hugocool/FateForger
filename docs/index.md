---
title: FateForger
---
# FateForger

*Being productive is no longer optional.* FateForger is an agent-based productivity suite that plans your day, haunts incomplete tasks, and syncs with Google Calendar and Slack. It runs as a set of cooperative agents orchestrated with AutoGen and backed by an MCP server.

## Quick Start

```bash
# Clone and install
poetry install
cp .env.template .env
# Initialize database and start services
poetry run python scripts/init_db.py
./run.sh
```

For installation details see [Setup](setup/installation.md).

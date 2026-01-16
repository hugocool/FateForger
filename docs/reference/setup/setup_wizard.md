# Setup & Diagnostics Wizard (Production)

This repo includes a small web UI that helps you:

- enter/persist credentials into `.env`
- upload required secret files (Google OAuth JSON)
- run connectivity checks for MCP servers using AutoGen MCP tool discovery
- validate Slack credentials (and optionally Socket Mode handshake)

The wizard is designed for VM + `docker compose` deployments where the main address should immediately show whether everything is configured correctly.

The `setup-wizard` service mounts the repo root at `/config`, so it can create/update `.env` and files under `secrets/`.

## What it covers

- Google Calendar MCP: https://github.com/nspady/google-calendar-mcp
- Notion MCP server: https://github.com/makenotion/notion-mcp-server
- TickTick MCP: https://github.com/JakobGruen/ticktick-mcp
- Slack Bolt (Socket Mode reference): https://github.com/slackapi/bolt-python

## Configure

In `.env` (start from `.env.template`):

- `WIZARD_HOST_PORT` (default `80`)
- `WIZARD_ADMIN_TOKEN` (required)
- `WIZARD_SESSION_SECRET` (required)

## Run

Start the stack:

- `docker compose up -d --build`

Then visit:

- `http://<vm-host>/`

Login with `WIZARD_ADMIN_TOKEN`.

## Notes

- After changing `.env` values, restart the affected containers.
- TickTick requires an interactive OAuth flow; the wizard guides you to run the auth container.

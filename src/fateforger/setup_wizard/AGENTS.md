# Setup Wizard Agent Notes

## Purpose

This folder contains the **Setup & Diagnostics wizard** (FastAPI) that provides a browser-based, production-focused setup flow for:

- Slack (Socket Mode) credentials validation
- Google Calendar MCP (OAuth JSON upload + connectivity checks)
- Notion MCP (NOTION_TOKEN + HTTP bearer auth token)
- TickTick MCP (client id/secret + guided OAuth flow)

The wizard is meant to be reachable at the VM's **main address** and act as a single place to verify whether the stack is correctly configured.

## Security model (production)

- The wizard is protected by an admin login:
  - `WIZARD_ADMIN_TOKEN` (required)
  - `WIZARD_SESSION_SECRET` (required)
- The wizard can write secrets into host-mounted files:
  - `.env` (via `WIZARD_ENV_PATH`)
  - `secrets/` (via `WIZARD_SECRETS_DIR`)

Treat the wizard as an **admin console**. Do not expose it publicly without additional perimeter controls (VPN, IP allowlist, auth gateway).

## How checks work

- MCP checks use AutoGen's MCP integration:
  - `autogen_ext.tools.mcp.mcp_server_tools(StreamableHttpServerParams(...))`
  - This avoids manual MCP HTTP calls while still validating MCP connectivity.
- Slack check uses Slack Web API `auth.test` and (optionally) a brief Socket Mode connect/disconnect.

## Upstream docs links

- Google Calendar MCP: https://github.com/nspady/google-calendar-mcp
- Notion MCP server: https://github.com/makenotion/notion-mcp-server
- TickTick MCP: https://github.com/JakobGruen/ticktick-mcp
- Slack Bolt Python: https://github.com/slackapi/bolt-python

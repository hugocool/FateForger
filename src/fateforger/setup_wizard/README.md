# Setup Wizard

FastAPI setup and diagnostics UI for Slack and MCP services. It is meant to be reachable at the
VM's **main address** and act as a single place to verify whether the stack is correctly
configured, for:

- Slack (Socket Mode) credentials validation
- Google Calendar MCP (OAuth JSON upload + connectivity checks)
- Notion MCP (`NOTION_TOKEN` + HTTP bearer auth token)
- TickTick MCP (client id/secret + guided OAuth flow)
- Toggl MCP (API token + workspace visibility)

Key files:
- `app.py`: FastAPI routes and views.
- `checks.py`: health checks for integrations.
- `envfile.py`: environment file updates.
- `templates/`: Jinja templates for setup pages.

## Security model (production)

The wizard is protected by an admin login — `WIZARD_ADMIN_TOKEN` and `WIZARD_SESSION_SECRET` are
both required — and it can write secrets into host-mounted files: `.env` (via `WIZARD_ENV_PATH`)
and `secrets/` (via `WIZARD_SECRETS_DIR`).

**Treat the wizard as an admin console.** Do not expose it publicly without additional perimeter
controls (VPN, IP allowlist, auth gateway).

## How the checks work

- MCP checks go through AutoGen's MCP integration —
  `autogen_ext.tools.mcp.mcp_server_tools(StreamableHttpServerParams(...))` — which validates MCP
  connectivity without hand-rolling MCP HTTP calls.
- The Slack check uses the Web API `auth.test` and, optionally, a brief Socket Mode
  connect/disconnect.

## Upstream docs

- Google Calendar MCP: https://github.com/nspady/google-calendar-mcp
- Notion MCP server: https://github.com/makenotion/notion-mcp-server
- TickTick MCP: https://github.com/JakobGruen/ticktick-mcp
- Toggl MCP server: https://github.com/verygoodplugins/mcp-toggl
- Slack Bolt Python: https://github.com/slackapi/bolt-python

# Slackbot

Slack/Bolt integration that routes Slack DMs / @mentions into the AutoGen runtime.
Each Slack thread binds to a specific AutoGen agent (temporary "focus") with a TTL.

## Commands
- `/ff-focus <agent_type> [note]` — bind this thread to a specific agent.
- `/ff-clear` — clear the focus for this thread.
- `/ff-status` — show current focus and allowed agents.

## Env
- SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
- SLACK_APP_TOKEN (Socket Mode), SLACK_SOCKET_MODE=true
- SLACK_PORT=3000 (if not using Socket Mode)
- SLACK_FOCUS_TTL=7200

## Run
```bash
poetry run python -m fateforger.slackbot.bot

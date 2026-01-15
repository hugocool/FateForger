---
title: Installation
---

Copy `.env.template` to `.env` and fill in:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `OPENAI_API_KEY`
- `MCP_ENDPOINT`
- `DATABASE_URL`
- Optional (durable timeboxing preference memory in Notion):
  - `NOTION_TOKEN`
  - `NOTION_TIMEBOXING_PARENT_PAGE_ID`

Start services with Docker Compose:

```bash
docker-compose up -d calendar-mcp fateforger
```

## Using Timeboxing in Slack

- In a DM with the bot, ask for a schedule: `timebox tomorrow` / `plan tomorrow with time blocks`.
- In any thread/channel, you can force routing with `/ff-focus timeboxing_agent`, then send your request in that thread.

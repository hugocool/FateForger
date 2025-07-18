---
title: Installation
---

Copy `.env.template` to `.env` and fill in:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `OPENAI_API_KEY`
- `MCP_ENDPOINT`
- `DATABASE_URL`

Start services with Docker Compose:

```bash
docker-compose up -d calendar-mcp fateforger
```

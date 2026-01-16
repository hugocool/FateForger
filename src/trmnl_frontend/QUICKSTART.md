# TRMNL Frontend Development

## Quick Start (One Click)

**Press `Cmd+Shift+B` or run task: "FateForger: TRMNL Dev Server"**

This starts:
- TRMNL preview server with hot reload
- File watcher for `src/full.liquid` and `src/data.json`
- Live preview at **http://localhost:4567**

## What You Can Edit

### src/full.liquid
Changes automatically refresh the preview. Uses TRMNL Framework v2 components.

### src/data.json
Changes automatically reload. Test different scenarios:
- Block types (DW, M, SW, H, R, BU, PR, BG)
- Time ranges and progress
- Task pipelines
- Metrics (Deep Work, velocity)

## Port Info

- **Preview**: http://localhost:4567
- **No conflicts**: calendar-mcp (3000), notion-mcp (3001), slack-bot (3002)

## Stopping

Press `Ctrl+C` in the terminal, or run:
```bash
cd src/trmnl_frontend
docker compose down
```

## Full Documentation

See [README.md](README.md) for:
- Data contract / JSON schema
- Backend integration guide
- Framework v2 component reference
- Testing scenarios

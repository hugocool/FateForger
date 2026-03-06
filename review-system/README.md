# Review System

A weekly review agent with a Notion backend. Conducts Socratic, gated review sessions and writes to Notion incrementally as each phase is met.

## Architecture

```
Agent (AutoGen / Claude Desktop / browser app)
    │
    ▼
MCP Server (FastMCP — Context Triad)
    ├── Tools     — 8 CRUD operations
    ├── Resource  — mcp://review/guidelines (serves SKILL.md)
    └── Prompt    — review_session template
    │
    ▼
Tool Layer (stateless functions)
    │
    ▼
ORM Layer (ultimate-notion models)
    │
    ▼
Notion (Weekly Reviews DB + Outcomes DB)
```

## Quick Start

### 1. Clone and configure
```bash
git clone <repo> && cd review-system
cp .env.example .env
# Fill in NOTION_TOKEN, WEEKLY_REVIEWS_DB_ID, OUTCOMES_DB_ID, ANTHROPIC_API_KEY
```

### 2. Create Notion databases
Run the setup wizard (app/App.jsx) or:
```bash
NOTION_TOKEN=ntn_... python notion_schema/init_db.py --page-id YOUR_PAGE_ID
```

### 3. Implement ORM layer
Fill in models/ and tools/ using ultimate-notion, then:
```bash
pip install -r requirements.txt
```

### 4. Run the server
```bash
docker compose up                                          # Docker (SSE)
python -m mcp.server                                       # stdio
python -m mcp.server --transport sse --port 8000           # SSE local
```

### 5. Connect AutoGen
See docs/autogen_integration.md — three modes: McpWorkbench+Docker, StdioMCP, direct import.

## What needs implementing

MCP server, Docker, AutoGen integration, SKILL.md, and React app are complete.
These four files need ultimate-notion wiring:

1. models/weekly_review.py
2. models/outcome.py
3. tools/read.py
4. tools/write.py

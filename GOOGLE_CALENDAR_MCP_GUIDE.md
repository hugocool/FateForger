# 📅 Google Calendar MCP Query Guide

## Overview

You have **3 main implementations** for querying Google Calendar via MCP. The **MOST MATURE** implementation is in `CalendarHaunter`.

---

## 🏆 Most Mature: `CalendarHaunter` (RECOMMENDED)

**Location:** `/src/fateforger/agents/admonisher/calendar.py`

### What It Does
- Uses **AutoGen AssistantAgent** with real MCP tools
- Queries Google Calendar via **StreamableHttpServerParams** (HTTP transport)
- Provides 9 calendar operations: list, create, update, delete, search, etc.
- Fully integrated into the FateForger bot system

### Key Methods

```python
await haunter.get_todays_events()          # What events today?
await haunter.get_weekly_schedule()        # Week's schedule
await haunter.list_calendars()             # All calendars
await haunter.search_events(query)         # Search by keyword
await haunter.ask_calendar_question(q)     # Raw natural language query
```

### Key Features
✅ **Production-Ready**
- Proper error handling and logging
- Real MCP tool loading via HTTP
- Lazy initialization of agent
- Integrated with Slack bot system

### Configuration
```bash
MCP_CALENDAR_SERVER_URL=http://localhost:3000    # Your MCP server
OPENAI_API_KEY=sk-...                            # For agent responses
```

### Example Usage
```python
from fateforger.agents.admonisher.calendar import CalendarHaunter

haunter = CalendarHaunter(
    session_id=123,
    slack=slack_client,
    scheduler=scheduler,
    channel="C123456"
)

# Query calendar
events = await haunter.get_todays_events()
print(events)  # ✅ Real calendar data from Google
```

---

## 2️⃣ Archive Implementation (Pre-Production)

**Location:** `/archive/productivity_bot/mcp_integration.py`

### What It Does
- Lower-level **CalendarMcpClient** class
- Direct tool calling: `list_events()`, `create_event()`, `update_event()`, etc.
- Parses JSON responses from MCP server manually
- Uses **McpWorkbench** (older abstraction)

### Key Methods
```python
events = await client.list_events(start_date, end_date)
event = await client.create_event(title, start_time, end_time)
await client.update_event(event_id, title=new_title)
await client.delete_event(event_id)
```

### Differences from Mature Version
⚠️ **Not Recommended** - Pre-production code
- Uses older `McpWorkbench` instead of AutoGen tools
- Manual JSON parsing (error-prone)
- No natural language interface (raw tool calls)
- Less integrated with bot system

### Why It's in `/archive/`
This was an earlier attempt before AutoGen's MCP integration matured. The CalendarHaunter replaces this approach with native AutoGen support.

---

## 3️⃣ Notebook Implementations (Development/Testing)

**Locations:**
- `/notebooks/minimal_working_mcp.ipynb` - ✅ **Working reference**
- `/notebooks/clean_working_mcp.ipynb` - ✅ **Clean implementation**
- `/notebooks/caniconal_agent.ipynb` - Development notes

### What They Do
- Demonstrate end-to-end MCP calendar integration
- Test patterns for AutoGen + MCP
- Interactive calendar agent examples
- Real Google Calendar queries with agent responses

### Example from `minimal_working_mcp.ipynb`

```python
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

# 1. Connect to MCP server
params = StreamableHttpServerParams(
    url="http://localhost:3000",
    timeout=10.0
)

# 2. Load real calendar tools
tools = await mcp_server_tools(params)

# 3. Create agent with tools
agent = AssistantAgent(
    name="CalendarAgent",
    model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"),
    system_message=f"Today is {date.today()}. Use calendar tools to help.",
    tools=tools
)

# 4. Ask questions
response = await agent.on_messages([
    TextMessage(content="What events do I have today?", source="user")
])
```

---

## 🎯 Which Implementation to Use?

| Use Case | Recommendation |
|----------|---|
| **New feature in FateForger** | Use `CalendarHaunter` |
| **Direct tool calls (no LLM)** | Adapt archive `CalendarMcpClient` |
| **Testing/Development** | Use notebooks as reference |
| **Production bot** | `CalendarHaunter` ✅ |

---

## 📊 Comparison Matrix

| Feature | CalendarHaunter | Archive Client | Notebooks |
|---------|---|---|---|
| **Status** | ✅ Production | ⚠️ Archive | 📚 Reference |
| **Transport** | HTTP (Streamable) | McpWorkbench | HTTP (Streamable) |
| **Natural Language** | ✅ Yes (LLM) | ❌ No | ✅ Yes |
| **Tool Discovery** | Auto via AutoGen | Manual JSON | Auto via AutoGen |
| **Error Handling** | ✅ Robust | ⚠️ Basic | Basic |
| **Slack Integration** | ✅ Built-in | ❌ None | ❌ None |
| **Testing** | ✅ Unit tests | ❌ None | ✅ Working |

---

## 🔧 Implementation Details

### CalendarHaunter Architecture

```
User Question
    ↓
CalendarHaunter.ask_calendar_question()
    ↓
AutoGen AssistantAgent
    ↓
MCP Tools (9 available)
    ↓
Google Calendar API
    ↓
Response → Natural Language Answer
```

### Available MCP Tools (from Google Calendar MCP server)
1. `list-calendars` - List available calendars
2. `list-events` - List events in date range
3. `search-events` - Search by keyword
4. `get-event` - Get event details
5. `create-event` - Create new event
6. `update-event` - Update event
7. `delete-event` - Delete event
8. `get-freebusy` - Check availability
9. `get-current-time` - Server time

---

## ✅ Quick Start

### 1. Use CalendarHaunter in Your Code

```python
from fateforger.agents.admonisher.calendar import CalendarHaunter

# Create haunter (usually done by scheduler)
haunter = CalendarHaunter(session_id=..., slack=..., scheduler=..., channel=...)

# Query calendar
response = await haunter.ask_calendar_question(
    "Show me all my meetings this week"
)
print(response)  # Full natural language response
```

### 2. Run MCP Server

```bash
# Docker (recommended)
docker run -it \
  -e GOOGLE_CALENDAR_CREDENTIALS_PATH=/secrets/gcal-oauth.json \
  -p 3000:3000 \
  nspady/google-calendar-mcp

# Or locally if available
python -m mcp_calendar_server --port 3000
```

### 3. Set Environment Variables

```bash
export MCP_CALENDAR_SERVER_URL=http://localhost:3000
export OPENAI_API_KEY=sk-...
```

### 4. Query!

```bash
poetry run python -c "
import asyncio
from fateforger.agents.admonisher.calendar import CalendarHaunter

async def test():
    haunter = CalendarHaunter(123, None, None, 'test')
    print(await haunter.get_todays_events())

asyncio.run(test())
"
```

---

## 🐛 Debugging

### Check MCP Connection
```python
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

params = StreamableHttpServerParams(url="http://localhost:3000", timeout=10)
tools = await mcp_server_tools(params)
print(f"✅ Loaded {len(tools)} tools")
```

### Check Calendar Access
```python
response = await haunter.ask_calendar_question("List all my calendars")
print(response)  # Should show calendar names
```

### Common Issues

| Issue | Solution |
|-------|----------|
| "No MCP tools loaded" | Ensure MCP server is running on correct URL |
| "OpenAI API key not configured" | Set `OPENAI_API_KEY` environment variable |
| "Connection timeout" | Check MCP_CALENDAR_SERVER_URL, increase timeout |
| "Google auth failed" | Ensure OAuth credentials are valid |

---

## 📚 Related Files

- **Main implementation:** `/src/fateforger/agents/admonisher/calendar.py`
- **Tool configuration:** `/src/fateforger/tools/calendar_mcp.py`
- **Tests:** `/tests/unit/test_calendar_haunter.py`
- **Config:** `/src/fateforger/core/config.py` (MCP settings)
- **Base class:** `/src/fateforger/agents/admonisher/base.py`

---

## 🎓 Learning Path

1. **Understand the pattern:** Read `CalendarHaunter` class
2. **See it working:** Check `minimal_working_mcp.ipynb`
3. **Run tests:** `poetry run pytest tests/unit/test_calendar_haunter.py`
4. **Integrate:** Use `CalendarHaunter` in your Slack handler
5. **Extend:** Add custom prompts or calendar-specific logic

---

**Last Updated:** December 10, 2025
**Status:** ✅ CalendarHaunter is production-ready

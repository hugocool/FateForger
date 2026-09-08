# Google Calendar MCP Query Locations - Complete Inventory

> **Superseded (2026-09).** This document calls `CalendarHaunter`
> (`src/fateforger/agents/admonisher/calendar.py`) production. Nothing has
> constructed it since haunting moved to `fateforger/haunt/` — see
> `src/fateforger/haunt/README.md` for the current reminder/reconciliation
> orchestration and `src/fateforger/haunt/reconcile.py` for the calendar
> access it uses instead. `admonisher/base.py` and `admonisher/calendar.py`
> have no caller in `src/` or `scripts/`; their tests were removed in the
> 2026-09 test-suite prune (`tests/README.md`, "What's out of `tests/unit/`").
> Everything below is left as written, as a record of what was true then —
> it is not a guide to the current architecture.

## 🎯 Summary

You have Google Calendar MCP integration in **3 locations**, with the **CalendarHaunter** being the most mature production-ready implementation.

---

## 1️⃣ PRODUCTION (Recommended)

### Location: `src/fateforger/agents/admonisher/calendar.py`
- **Status:** ✅ **MATURE - USE THIS**
- **Type:** Main implementation class
- **Integration:** Native AutoGen + MCP
- **Transport:** HTTP (StreamableHttpServerParams)

#### What it does:
- Wraps AutoGen AssistantAgent with real Google Calendar MCP tools
- Provides high-level methods: `get_todays_events()`, `get_weekly_schedule()`, etc.
- Uses natural language processing through OpenAI
- Fully integrated with Slack bot system

#### Key Methods:
```python
haunter = CalendarHaunter(session_id, slack, scheduler, channel)

# High-level queries
await haunter.get_todays_events()
await haunter.get_weekly_schedule()
await haunter.list_calendars()
await haunter.search_events(query)
await haunter.create_event(title, start_time, description)

# Low-level query
await haunter.ask_calendar_question("Your natural language question")
```

#### Supporting Files:
- `src/fateforger/tools/calendar_mcp.py` - MCP tool loader utility
- `src/fateforger/core/config.py` - Configuration (MCP_CALENDAR_SERVER_URL, etc.)
- `tests/unit/test_calendar_haunter.py` - Unit and integration tests

---

## 2️⃣ ARCHIVE (Pre-production/Reference)

### Location: `archive/productivity_bot/mcp_integration.py`
- **Status:** ⚠️ **ARCHIVE - For reference only**
- **Type:** Lower-level client class
- **Integration:** McpWorkbench (older)
- **Transport:** Raw tool calling

#### What it does:
- Direct MCP tool calling without natural language
- Manual JSON parsing from MCP responses
- Older approach before AutoGen's native MCP support

#### Key Methods:
```python
client = CalendarMcpClient()
await client.initialize()

# Direct tool calls
await client.list_events(start_date, end_date)
await client.create_event(title, start_time, end_time)
await client.update_event(event_id, ...)
await client.delete_event(event_id)
await client.search_events(query)
```

#### Why Archive:
- The CalendarHaunter replaces this with better AutoGen integration
- No natural language interface
- More error-prone manual parsing
- Less integrated with bot system

#### Related Archive Files:
- `archive/productivity_bot/agents/mcp_client.py` - MCP client utilities
- `archive/productivity_bot/mcp/workbench.py` - Workbench wrapper

---

## 3️⃣ DEVELOPMENT/TESTING (Reference)

### Location: `notebooks/` (Multiple notebooks)
- **Status:** 📚 **Development/Testing - Reference only**
- **Type:** Jupyter notebooks with working examples

#### Key Notebooks:

##### `notebooks/minimal_working_mcp.ipynb`
- ✅ **FULLY WORKING** reference implementation
- Shows complete AutoGen + MCP integration
- Real calendar queries with agent responses
- Tests all 9 available calendar tools

**What it demonstrates:**
```python
# Connect to MCP server
params = StreamableHttpServerParams(url="http://localhost:3000", timeout=10.0)
tools = await mcp_server_tools(params)

# Create AutoGen agent
agent = AssistantAgent(
    name="CalendarAgent",
    model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"),
    system_message=f"Today is {date.today()}...",
    tools=tools
)

# Ask questions
response = await agent.on_messages([
    TextMessage(content="What events do I have today?", source="user")
])
```

##### `notebooks/clean_working_mcp.ipynb`
- Clean, well-commented version of the same pattern
- Good for understanding the flow
- Has inline explanations

##### `notebooks/caniconal_agent.ipynb`
- Development notes and exploration
- Tests different MCP client approaches
- Debugging patterns

---

## 📊 Query Locations by Use Case

| Use Case | File | Method | Status |
|----------|------|--------|--------|
| **FateForger bot calendar queries** | `src/fateforger/agents/admonisher/calendar.py` | CalendarHaunter class | ✅ PROD |
| **Slack integration** | `src/fateforger/agents/admonisher/calendar.py` | CalendarHaunter.handle_reply() | ✅ PROD |
| **Direct calendar operations** | `archive/productivity_bot/mcp_integration.py` | CalendarMcpClient methods | ⚠️ ARCHIVE |
| **Testing/Development** | `notebooks/minimal_working_mcp.ipynb` | AutoGen agent directly | 📚 DEV |
| **Bot configuration** | `src/fateforger/core/config.py` | Settings class | ✅ PROD |

---

## 🔍 All Query Types in CalendarHaunter

### 1. Get Today's Events
```python
await haunter.get_todays_events()
# → "Today (2025-12-10) you have: Team Sync at 10am, 1-on-1 at 2pm, ..."
```

### 2. Get Weekly Schedule
```python
await haunter.get_weekly_schedule()
# → Full week view with all events
```

### 3. List All Calendars
```python
await haunter.list_calendars()
# → "You have 3 calendars: Work, Personal, Team..."
```

### 4. Search Events
```python
await haunter.search_events("meeting")
# → Lists all events with "meeting" in title/description
```

### 5. Create Event
```python
await haunter.create_event(
    title="Team Sync",
    start_time="2025-12-10 15:00",
    description="Weekly sync"
)
# → "Created event 'Team Sync' for Dec 10 at 3pm"
```

### 6. Natural Language Query
```python
await haunter.ask_calendar_question(
    "Do I have any free slots tomorrow afternoon?"
)
# → Flexible natural language response
```

---

## 🏗️ Architecture Overview

```
Slack User Question
        ↓
    [FateForger Bot]
        ↓
CalendarHaunter (main class)
    ├─ _ensure_agent()
    │   └─ _create_calendar_agent()
    │       ├─ StreamableHttpServerParams (HTTP transport)
    │       ├─ mcp_server_tools() (load 9 tools)
    │       └─ AssistantAgent (AutoGen agent)
    │
    ├─ ask_calendar_question() ← Core method
    │   └─ agent.on_messages() ← Calls MCP tools
    │
    ├─ get_todays_events() ─┐
    ├─ get_weekly_schedule()├─ Built on top of ask_calendar_question()
    ├─ list_calendars()    ─┤
    ├─ search_events()     ─┤
    └─ create_event()      ─┘
        
                ↓
            [MCP Tools]
                ↓
        [Google Calendar API]
```

---

## 📚 File Dependencies

### CalendarHaunter (Main)
```
src/fateforger/agents/admonisher/calendar.py
├─ imports: autogen_agentchat, autogen_ext.tools.mcp
├─ imports: fateforger.core.config (settings)
├─ imports: fateforger.agents.admonisher.base (BaseHaunter)
└─ exports: CalendarHaunter, create_calendar_haunter_agent()
```

### Tool Configuration
```
src/fateforger/tools/calendar_mcp.py
├─ imports: autogen_ext.tools.mcp
├─ exports: get_calendar_mcp_tools()
└─ uses: StreamableHttpServerParams, mcp_server_tools
```

### Configuration
```
src/fateforger/core/config.py
├─ MCP server URL
├─ MCP timeout
├─ OpenAI API key
└─ Other MCP settings
```

### Tests
```
tests/unit/test_calendar_haunter.py
├─ Unit tests (mocked MCP)
└─ Integration tests (real MCP server required)
```

---

## 🚀 Available MCP Tools

When you connect to the MCP server, you get access to 9 Google Calendar tools:

1. **list-calendars** - List available calendars
2. **list-events** - List events in date range
3. **search-events** - Full-text search
4. **get-event** - Get specific event
5. **create-event** - Create new event
6. **update-event** - Modify event
7. **delete-event** - Remove event
8. **get-freebusy** - Check availability
9. **get-current-time** - Get server time

All tools are loaded automatically by CalendarHaunter from the MCP server.

---

## ✅ Recommendation

**Use `CalendarHaunter` for all Google Calendar queries in FateForger.**

### Why:
- ✅ Production-ready
- ✅ Natural language interface
- ✅ Integrated with Slack bot
- ✅ Proper error handling
- ✅ Well-tested
- ✅ Follows project patterns

### How to Use:

```python
# In your bot handler
from fateforger.agents.admonisher.calendar import CalendarHaunter

haunter = CalendarHaunter(
    session_id=user_session_id,
    slack=slack_client,
    scheduler=scheduler,
    channel=slack_channel
)

response = await haunter.ask_calendar_question(user_question)
```

---

## 🔗 Quick Links

- **Main class:** `src/fateforger/agents/admonisher/calendar.py`
- **Tests:** `tests/unit/test_calendar_haunter.py`
- **Example code:** `examples/calendar_queries.py` (new)
- **Complete guide:** `GOOGLE_CALENDAR_MCP_GUIDE.md` (new)
- **Config:** `src/fateforger/core/config.py`

---

**Last Updated:** December 10, 2025

# Migration Guide: Archive to CalendarHaunter

If you have code using the old `CalendarMcpClient` from the archive, here's how to migrate to the mature `CalendarHaunter` implementation.

---

## Migration Decision Tree

```
Do you need natural language responses?
├─ YES → Use CalendarHaunter (recommended) ✅
└─ NO  → Still use CalendarHaunter (better error handling)

Are you in the bot system (Slack)?
├─ YES → Must use CalendarHaunter ✅
└─ NO  → Can use either, but CalendarHaunter is simpler

Need direct tool calls without LLM?
├─ YES → Adapt archive code (but consider LLM)
└─ NO  → Definitely use CalendarHaunter ✅
```

---

## Before & After Examples

### Example 1: List Events

**OLD (Archive):**
```python
from archive.productivity_bot.mcp_integration import CalendarMcpClient

client = CalendarMcpClient()
await client.initialize()

events = await client.list_events(
    start_date="2025-12-10T00:00:00Z",
    end_date="2025-12-11T00:00:00Z",
    calendar_id="primary"
)

for event in events:
    print(f"{event['summary']}: {event['start']} - {event['end']}")

await client.cleanup()
```

**NEW (CalendarHaunter):**
```python
from fateforger.agents.admonisher.calendar import CalendarHaunter

haunter = CalendarHaunter(session_id=123, slack=slack_client, scheduler=scheduler, channel="C123")

events = await haunter.ask_calendar_question(
    "List all my events on December 10th, 2025"
)

print(events)  # ← Natural language formatted response
```

**Advantages:**
- ✅ No manual initialization/cleanup
- ✅ Natural language output (already formatted for users)
- ✅ Simpler error handling
- ✅ Works with Slack integration

---

### Example 2: Create Event

**OLD (Archive):**
```python
client = CalendarMcpClient()
await client.initialize()

event = await client.create_event(
    title="Team Sync",
    start_time="2025-12-10T15:00:00Z",
    end_time="2025-12-10T16:00:00Z",
    description="Weekly team synchronization",
    location="Conference Room A"
)

if event:
    print(f"Created event: {event['id']}")
else:
    print("Failed to create event")

await client.cleanup()
```

**NEW (CalendarHaunter):**
```python
haunter = CalendarHaunter(123, slack_client, scheduler, "C123")

response = await haunter.create_event(
    title="Team Sync",
    start_time="2025-12-10 15:00",
    description="Weekly team synchronization"
)

print(response)  # ← Confirmation message already formatted
```

**Advantages:**
- ✅ Simpler parameters (no Z timestamps, just dates)
- ✅ Automatic confirmation message
- ✅ No null checking needed
- ✅ Location can be in description

---

### Example 3: Search Events

**OLD (Archive):**
```python
client = CalendarMcpClient()
await client.initialize()

events = await client.list_events(start_date="2025-12-01", end_date="2025-12-31")

# Manual filtering
matching = [
    e for e in events 
    if "meeting" in e.get('summary', '').lower() 
    or "meeting" in e.get('description', '').lower()
]

for event in matching:
    print(f"{event['summary']}")

await client.cleanup()
```

**NEW (CalendarHaunter):**
```python
haunter = CalendarHaunter(123, slack_client, scheduler, "C123")

response = await haunter.search_events("meeting")

print(response)  # ← All matching events, naturally formatted
```

**Advantages:**
- ✅ No manual iteration
- ✅ No manual filtering
- ✅ Smarter matching (uses MCP search-events tool)
- ✅ Consistent formatting

---

### Example 4: Complex Query

**OLD (Archive):**
```python
client = CalendarMcpClient()
await client.initialize()

# Multiple calls to build query
today = date.today()
events = await client.list_events(
    start_date=today.isoformat(),
    end_date=(today + timedelta(days=1)).isoformat()
)

free_time = 480  # 8 hours in minutes
free_slots = []

events_sorted = sorted(events, key=lambda e: e['start'])
last_end = 9 * 60  # 9 AM in minutes

for event in events_sorted:
    start_minutes = parse_time_to_minutes(event['start'])
    gap = start_minutes - last_end
    if gap >= 60:  # At least 1 hour
        free_slots.append((last_end, start_minutes))
    
    last_end = parse_time_to_minutes(event['end'])

await client.cleanup()
```

**NEW (CalendarHaunter):**
```python
haunter = CalendarHaunter(123, slack_client, scheduler, "C123")

response = await haunter.ask_calendar_question(
    "Do I have any free slots tomorrow afternoon that are at least 1 hour long?"
)

print(response)  # ← LLM interprets and answers naturally
```

**Advantages:**
- ✅ No manual time parsing
- ✅ No loop iteration
- ✅ Natural language question
- ✅ LLM understands context (afternoon = 12pm-6pm)
- ✅ Handles edge cases naturally

---

## Migration Steps

### Step 1: Find All Archive Usage

```bash
# Search for old import
grep -r "from archive.productivity_bot.mcp_integration import" . --include="*.py"

# Search for direct CalendarMcpClient usage
grep -r "CalendarMcpClient" . --include="*.py"
```

### Step 2: Identify What Each Does

For each usage, understand:
- What calendar operation is being done?
- Is it in bot code (Slack)?
- Does it need direct tool access?
- Can it work with natural language?

### Step 3: Replace One at a Time

```python
# OLD
from archive.productivity_bot.mcp_integration import CalendarMcpClient
client = CalendarMcpClient()
await client.initialize()
events = await client.list_events(...)
await client.cleanup()

# NEW
from fateforger.agents.admonisher.calendar import CalendarHaunter
haunter = CalendarHaunter(session_id, slack, scheduler, channel)
response = await haunter.ask_calendar_question("What events do I have?")
```

### Step 4: Test

```bash
# Run your code
MCP_CALENDAR_SERVER_URL=http://localhost:3000 \
OPENAI_API_KEY=sk-... \
poetry run python your_script.py
```

### Step 5: Delete Archive Code

```bash
# Once migration is complete, you can delete
rm -rf archive/productivity_bot/mcp_*
```

---

## Equivalence Table

| Archive Code | CalendarHaunter Equivalent | Notes |
|---|---|---|
| `client.list_events(start, end, cal_id)` | `ask_calendar_question("What events...")` | Use natural language |
| `client.create_event(...)` | `create_event(title, start, description)` | Simpler API |
| `client.search_events(q)` | `search_events(query)` | Same functionality, better |
| `client.update_event(...)` | `ask_calendar_question("Update...")` | Use natural language |
| `client.delete_event(...)` | `ask_calendar_question("Delete...")` | Use natural language |
| `client.get_event(id)` | `ask_calendar_question("Get event X")` | Use natural language |
| `client.initialize()` | (automatic in haunter) | No need for manual init |
| `client.cleanup()` | (automatic in haunter) | No need for manual cleanup |

---

## Special Cases

### Case 1: You Need Raw Tool Response (No LLM)

If you absolutely need the raw JSON response (rare):

```python
# You could still adapt the archive code, but better to use:
from fateforger.tools.calendar_mcp import get_calendar_mcp_tools
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

# Get raw tools
params = StreamableHttpServerParams(url="http://localhost:3000", timeout=10)
tools = await mcp_server_tools(params)

# Find the tool you want
list_events_tool = next(t for t in tools if 'list-events' in t.name)

# Call it (advanced usage)
result = await list_events_tool.run_json({"calendarId": "primary", ...})
```

### Case 2: High-Volume Batch Processing

If you're processing hundreds of queries:

```python
# Still use CalendarHaunter, but reuse same haunter instance:
haunter = CalendarHaunter(session_id, slack, scheduler, channel)

questions = ["What events today?", "Free slots tomorrow?", ...]
responses = await asyncio.gather(
    *[haunter.ask_calendar_question(q) for q in questions]
)

# Haunter caches agent, so it's efficient
```

### Case 3: Tests (Mocking)

```python
# Your tests can still mock CalendarHaunter
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_calendar_integration():
    with patch('fateforger.agents.admonisher.calendar.CalendarHaunter') as mock:
        mock_haunter = AsyncMock()
        mock_haunter.ask_calendar_question.return_value = "Mocked response"
        
        # Your code
        result = await mock_haunter.ask_calendar_question("test")
        assert "Mocked" in result
```

---

## Checklist for Migration

- [ ] Found all archive MCP imports
- [ ] Documented each usage
- [ ] Created CalendarHaunter equivalents
- [ ] Tested in dev environment
- [ ] Updated any bot handlers
- [ ] Verified Slack integration still works
- [ ] Removed old archive imports
- [ ] Ran full test suite
- [ ] Deleted archive files (optional)

---

## Rollback Plan

If something goes wrong:

1. **Keep archive code** (don't delete until proven)
2. **Run both in parallel** initially (if safe)
3. **Compare outputs** for correctness
4. **Migrate gradually** by feature/handler
5. **Keep git history** for reference

```bash
# If you deleted archive, you can recover:
git checkout HEAD -- archive/productivity_bot/mcp_*
```

---

## FAQ

**Q: Will CalendarHaunter be slower?**
A: No, it's actually faster (caches agent). The LLM just formats responses.

**Q: Do I lose control with natural language?**
A: No, the LLM is deterministic. You can add system prompts if needed.

**Q: What if I need exact JSON response?**
A: Ask in a way that returns JSON: `"List events as JSON: [...]"`

**Q: Is this a breaking change?**
A: No, CalendarHaunter is backward compatible. You can use both temporarily.

**Q: How do I handle errors?**

```python
try:
    response = await haunter.ask_calendar_question(question)
except RuntimeError as e:
    # MCP server issues
    logger.error(f"Calendar service down: {e}")
    return "Calendar service temporarily unavailable"
except Exception as e:
    # Other errors
    logger.error(f"Unexpected error: {e}")
    return "Something went wrong with calendar"
```

---

## Timeline

- **Week 1:** Identify all archive usage (search & document)
- **Week 2:** Create CalendarHaunter equivalents
- **Week 3:** Test and verify correctness
- **Week 4:** Deploy and monitor
- **After:** Delete archive code

---

**Questions?** Check:
- `GOOGLE_CALENDAR_MCP_GUIDE.md` - Complete reference
- `examples/calendar_queries.py` - Working code samples
- `src/fateforger/agents/admonisher/calendar.py` - Source code
- `tests/unit/test_calendar_haunter.py` - Test examples

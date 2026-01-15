# 📚 Google Calendar MCP Documentation Index

## Quick Navigation

### 🚀 **Start Here**
- **[QUICK_REFERENCE.txt](QUICK_REFERENCE.txt)** - One-page visual cheat sheet
- **[README_CALENDAR_MCP.md](README_CALENDAR_MCP.md)** - Executive summary

### 📖 **Main Documentation**
- **[GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md)** - Complete reference guide
  - Architecture overview
  - Configuration instructions
  - Quick start guide
  - Available tools
  - Debugging tips

### 📋 **Detailed Information**
- **[CALENDAR_QUERY_LOCATIONS.md](CALENDAR_QUERY_LOCATIONS.md)** - File inventory
  - All 3 implementation locations
  - What each does
  - Use case matrix
  - File dependencies

### 🔄 **Migration Guide**
- **[MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md](MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md)** - Upgrade path
  - Before/after code examples
  - Step-by-step migration
  - Special cases
  - Rollback plan

### 💡 **Code Examples**
- **[examples/calendar_queries.py](examples/calendar_queries.py)** - Working patterns
  - Direct usage
  - Search events
  - Natural language queries
  - Event creation
  - Bot integration
  - Batch processing
  - Error handling

---

## 🎯 By Use Case

**I want to...**

| Goal | Resource |
|------|----------|
| **Understand what I have** | [CALENDAR_QUERY_LOCATIONS.md](CALENDAR_QUERY_LOCATIONS.md) |
| **Get started quickly** | [QUICK_REFERENCE.txt](QUICK_REFERENCE.txt) |
| **See working code** | [examples/calendar_queries.py](examples/calendar_queries.py) |
| **Configure and setup** | [GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md) |
| **Upgrade from old code** | [MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md](MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md) |
| **Understand architecture** | [GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md#-implementation-details) |
| **Debug issues** | [GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md#-debugging) |

---

## 📂 Implementation Locations

### Production Implementation ✅
```
src/fateforger/agents/admonisher/calendar.py
├─ Class: CalendarHaunter
├─ Status: Production-Ready
└─ Use: CalendarHaunter for all Google Calendar queries
```

**Supporting files:**
- `src/fateforger/tools/calendar_mcp.py` - Tool loader
- `src/fateforger/core/config.py` - Configuration
- `tests/unit/test_calendar_haunter.py` - Tests

### Archive Implementation ⚠️
```
archive/productivity_bot/mcp_integration.py
├─ Class: CalendarMcpClient
├─ Status: Pre-production (archived)
└─ Use: Reference only, migrate to CalendarHaunter
```

### Development Reference 📚
```
notebooks/minimal_working_mcp.ipynb
├─ Status: Working examples
└─ Use: Learning and testing patterns
```

---

## 🛠️ Key Methods

### CalendarHaunter API

```python
from fateforger.agents.admonisher.calendar import CalendarHaunter

# Create
haunter = CalendarHaunter(session_id, slack, scheduler, channel)

# Query
await haunter.get_todays_events()        # Today's events
await haunter.get_weekly_schedule()      # Week's schedule
await haunter.list_calendars()           # All calendars
await haunter.search_events(query)       # Search by keyword
await haunter.create_event(...)          # Create event
await haunter.ask_calendar_question(...) # Natural language query
```

---

## 📊 Status & Recommendations

| Implementation | Status | Use For | Recommendation |
|---|---|---|---|
| **CalendarHaunter** | ✅ Production | All new code | ✅ **RECOMMENDED** |
| **CalendarMcpClient** | ⚠️ Archive | Legacy code only | ❌ Migrate away |
| **Notebook examples** | 📚 Development | Learning | 📚 Reference |

---

## 🔌 Setup

```bash
# Environment variables
export MCP_CALENDAR_SERVER_URL=http://localhost:3000
export OPENAI_API_KEY=sk-your-api-key

# Start MCP server
docker run -it \
  -e GOOGLE_CALENDAR_CREDENTIALS_PATH=/secrets/gcal-oauth.json \
  -p 3000:3000 \
  nspady/google-calendar-mcp
```

---

## 📝 Document Overview

### QUICK_REFERENCE.txt (⚡ Quick Lookup)
- One-page visual reference
- Key methods
- Basic usage
- Troubleshooting
- Perfect for printing

**Best for:** Quick lookup, getting oriented

---

### README_CALENDAR_MCP.md (📄 Summary)
- Executive summary
- TL;DR section
- Documentation file list
- Recommendations
- Implementation status

**Best for:** Overview and summary

---

### GOOGLE_CALENDAR_MCP_GUIDE.md (📖 Complete Guide)
- Overview of all implementations
- Architecture diagrams
- Configuration instructions
- Available MCP tools
- Debugging tips
- Quick start guide

**Best for:** Comprehensive understanding

---

### CALENDAR_QUERY_LOCATIONS.md (📋 Inventory)
- All 3 query locations
- File dependencies
- Use case matrix
- Available tools
- Comparison table

**Best for:** Understanding what exists where

---

### MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md (🔄 Upgrade Path)
- Before/after examples
- Step-by-step migration
- Equivalence table
- Special cases
- Rollback plan
- Checklist

**Best for:** Upgrading existing code

---

### examples/calendar_queries.py (💡 Working Code)
- 8 working patterns
- Copy-paste ready
- Different use cases
- Error handling
- Bot integration

**Best for:** Learning by example

---

## 🎓 Reading Order

**Quick Start (30 minutes):**
1. [QUICK_REFERENCE.txt](QUICK_REFERENCE.txt) - Get oriented
2. [examples/calendar_queries.py](examples/calendar_queries.py) - See code
3. Start using CalendarHaunter

**Comprehensive Understanding (2 hours):**
1. [README_CALENDAR_MCP.md](README_CALENDAR_MCP.md) - Summary
2. [GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md) - Full guide
3. [CALENDAR_QUERY_LOCATIONS.md](CALENDAR_QUERY_LOCATIONS.md) - Inventory
4. Review CalendarHaunter source code

**Migration from Archive (1 hour):**
1. [MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md](MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md) - Guide
2. [examples/calendar_queries.py](examples/calendar_queries.py) - Patterns
3. Implement changes

---

## ✨ Key Takeaways

✅ **Use CalendarHaunter** for all Google Calendar queries
- Production-ready
- Natural language interface
- Proper error handling
- Fully integrated

📁 **File locations:**
- Main: `src/fateforger/agents/admonisher/calendar.py`
- Archive: `archive/productivity_bot/mcp_integration.py`
- Dev: `notebooks/minimal_working_mcp.ipynb`

🚀 **Quick start:**
```python
haunter = CalendarHaunter(session_id, slack, scheduler, channel)
response = await haunter.ask_calendar_question("What events today?")
```

📚 **Documentation files:**
- GOOGLE_CALENDAR_MCP_GUIDE.md - Main reference
- CALENDAR_QUERY_LOCATIONS.md - File inventory
- MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md - Upgrade guide
- examples/calendar_queries.py - Working code
- README_CALENDAR_MCP.md - Summary
- QUICK_REFERENCE.txt - Cheat sheet

---

## 🔗 Cross-References

See also:
- Main bot documentation (README.md)
- Calendar haunter source code (src/fateforger/agents/admonisher/calendar.py)
- Configuration reference (src/fateforger/core/config.py)
- Tests (tests/unit/test_calendar_haunter.py)

---

**Last Updated:** December 10, 2025  
**Status:** ✅ Complete  
**Created:** GitHub Copilot AI Assistant  

---

## 📞 Quick Links

- **Main Implementation:** `src/fateforger/agents/admonisher/calendar.py`
- **Examples:** `examples/calendar_queries.py`
- **Tests:** `tests/unit/test_calendar_haunter.py`
- **Configuration:** `src/fateforger/core/config.py`

---

**Start with:** [QUICK_REFERENCE.txt](QUICK_REFERENCE.txt) or [GOOGLE_CALENDAR_MCP_GUIDE.md](GOOGLE_CALENDAR_MCP_GUIDE.md)

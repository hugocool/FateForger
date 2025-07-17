# AutoGen Planner Agent

The AutoGen Planner Agent provides AI-powered daily planning capabilities by integrating with the Model Context Protocol (MCP) server for calendar operations and using AutoGen's conversational AI to generate optimized daily schedules.

## Overview

The AutoGen integration consists of three main components:

1. **MCPCalendarTool** - Interface for calendar operations via MCP
2. **AutoGenPlannerAgent** - AI agent for plan generation and optimization
3. **PlannerBot Integration** - Slack bot integration with persistence and haunting

## Architecture

```mermaid
graph LR
    A[Slack User] --> B[PlannerBot Modal]
    B --> C[Planning Session DB]
    B --> D[AutoGen Agent]
    D --> E[MCP Calendar Tool]
    E --> F[MCP Server]
    F --> G[Google Calendar API]
    C --> H[Haunter Bot]
    D --> I[AI Enhancement Message]
    I --> A
```

## Features

### Calendar Integration

- **Event Listing**: Retrieve calendar events for specified date ranges
- **Availability Analysis**: Find available time slots between existing events
- **Event Creation**: Create new calendar events via MCP (planned)
- **Conflict Detection**: Identify scheduling conflicts and busy periods

### AI-Powered Planning

- **Goal Analysis**: Parse and prioritize user goals
- **Schedule Optimization**: Suggest optimal time slots based on calendar availability
- **Context-Aware Recommendations**: Provide planning advice based on work patterns
- **Time-Boxing Suggestions**: Generate detailed time-blocked schedules

### Slack Integration

- **Modal Forms**: Interactive planning session creation
- **AI Enhancement Messages**: Follow-up suggestions and recommendations
- **Action Buttons**: Apply AI suggestions or view detailed analysis
- **Persistent Sessions**: Link AI enhancements to database records

## Usage

### Basic Plan Generation

```python
from productivity_bot.autogen_planner import AutoGenPlannerAgent

# Initialize the agent
agent = AutoGenPlannerAgent()

# Generate a daily plan
plan = await agent.generate_daily_plan(
    user_id="U123456",
    goals="Complete project review, prepare presentation, team meeting",
    date_str="2025-07-16",
    preferences={
        "work_start": "09:00",
        "work_end": "17:00",
        "break_duration": 15
    }
)

print(plan["raw_plan"])
```

### Calendar Operations

```python
from productivity_bot.autogen_planner import MCPCalendarTool

# Initialize calendar tool
calendar = MCPCalendarTool()

# List today's events
events = await calendar.list_calendar_events(
    start_date="2025-07-16",
    end_date="2025-07-16"
)

# Find available time slots
slots = await calendar.get_available_time_slots(
    date_str="2025-07-16",
    duration_minutes=60,
    work_start="09:00",
    work_end="17:00"
)

# Create a new event
result = await calendar.create_calendar_event(
    title="Deep Work Session",
    start_time="2025-07-16T10:00:00Z",
    end_time="2025-07-16T11:30:00Z",
    description="Focused work time for project review"
)
```

### Session Enhancement

```python
# Enhance an existing planning session
enhanced = await agent.enhance_planning_session(
    session=planning_session,
    enhance_goals=True,
    suggest_schedule=True
)

print(enhanced["enhanced_schedule"])
print(enhanced["recommendations"])
```

## Configuration

### Environment Variables

```bash
# OpenAI API configuration
OPENAI_API_KEY=your_openai_api_key

# MCP server endpoint
MCP_ENDPOINT=http://mcp:4000

# Slack configuration (existing)
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
```

### MCP Server Setup

The AutoGen planner requires a running MCP server that provides calendar integration. The server should expose endpoints for:

- `/mcp` - Main MCP protocol endpoint
- Calendar operations (list, get, create events)
- Health check endpoint

Example MCP server configuration:

```yaml
# docker-compose.yml
services:
  mcp:
    image: your-mcp-server:latest
    ports:
      - "4000:4000"
    environment:
      - GOOGLE_CALENDAR_CREDENTIALS=/app/credentials.json
    volumes:
      - ./calendar-credentials.json:/app/credentials.json
```

## API Reference

### MCPCalendarTool

#### `list_calendar_events(start_date, end_date, max_results)`

List calendar events within a date range.

**Parameters:**
- `start_date` (str): Start date in YYYY-MM-DD format
- `end_date` (str): End date in YYYY-MM-DD format  
- `max_results` (int): Maximum events to return (default: 50)

**Returns:**
- `Dict[str, Any]`: Response with events list and metadata

#### `get_available_time_slots(date_str, duration_minutes, work_start, work_end)`

Find available time slots for scheduling.

**Parameters:**
- `date_str` (str): Date in YYYY-MM-DD format
- `duration_minutes` (int): Required duration in minutes
- `work_start` (str): Work day start time (HH:MM)
- `work_end` (str): Work day end time (HH:MM)

**Returns:**
- `Dict[str, Any]`: Available time slots with start/end times

#### `create_calendar_event(title, start_time, end_time, description)`

Create a new calendar event.

**Parameters:**
- `title` (str): Event title
- `start_time` (str): ISO format start time
- `end_time` (str): ISO format end time
- `description` (str): Optional event description

**Returns:**
- `Dict[str, Any]`: Created event data or error information

### AutoGenPlannerAgent

#### `generate_daily_plan(user_id, goals, date_str, preferences)`

Generate an AI-optimized daily plan.

**Parameters:**
- `user_id` (str): Slack user ID
- `goals` (str): User's goals for the day
- `date_str` (str): Target date (YYYY-MM-DD)
- `preferences` (Dict): User preferences (work hours, breaks, etc.)

**Returns:**
- `Dict[str, Any]`: Generated plan with structured and raw content

#### `enhance_planning_session(session, enhance_goals, suggest_schedule)`

Enhance an existing planning session with AI analysis.

**Parameters:**
- `session` (PlanningSession): Database session object
- `enhance_goals` (bool): Whether to enhance goals
- `suggest_schedule` (bool): Whether to suggest schedule

**Returns:**
- `Dict[str, Any]`: Enhancement suggestions and analysis

## Database Integration

### Planning Session Fields

The AutoGen integration adds AI-related fields to planning sessions:

```python
# Enhanced session notes include AI suggestions
session.notes += "\n\n## AI Suggestions:\n" + ai_plan

# Structured data can be stored in separate fields
session.ai_schedule_items = json.dumps(schedule_items)
session.ai_recommendations = json.dumps(recommendations)
```

### Haunter Integration

Planning sessions are automatically linked to the haunter system:

```python
# Schedule first reminder in 1 hour
first_reminder = datetime.utcnow() + timedelta(hours=1)
job_id = schedule_haunt(session_id, first_reminder, attempt=1)

# Store job ID for tracking
session.scheduler_job_id = job_id
session.next_nudge_attempt = 1
```

## Error Handling

The AutoGen system includes comprehensive error handling:

### Calendar Errors

```python
try:
    events = await calendar.list_calendar_events()
except Exception as e:
    logger.error(f"Calendar API error: {e}")
    return {"success": False, "error": str(e)}
```

### AI Generation Errors

```python
try:
    plan = await agent.generate_daily_plan(...)
except Exception as e:
    logger.error(f"Plan generation failed: {e}")
    # Fallback to simple template-based planning
```

### MCP Server Connectivity

```python
async def health_check_mcp():
    """Check MCP server health."""
    try:
        response = await mcp_query({"method": "health"})
        return response.get("status") == "healthy"
    except Exception:
        return False
```

## Testing

### Unit Tests

```bash
# Run AutoGen-specific tests
pytest tests/test_autogen_planner.py -v

# Run with coverage
pytest tests/test_autogen_planner.py --cov=src.productivity_bot.autogen_planner
```

### Integration Tests

```bash
# Test with mock MCP server
pytest tests/test_autogen_integration.py -v

# Test end-to-end flow
pytest tests/test_e2e_planning.py -v
```

### Manual Testing

```python
# Test plan generation
python -c "
import asyncio
from src.productivity_bot.autogen_planner import AutoGenPlannerAgent

async def test():
    agent = AutoGenPlannerAgent()
    plan = await agent.generate_daily_plan(
        user_id='test_user',
        goals='Test planning'
    )
    print(plan)

asyncio.run(test())
"
```

## Troubleshooting

### Common Issues

1. **MCP Server Connection Failed**
   - Check if MCP server is running on the configured port
   - Verify network connectivity and firewall settings
   - Check MCP server logs for errors

2. **OpenAI API Errors**
   - Verify API key is valid and has sufficient credits
   - Check rate limiting and usage quotas
   - Ensure model availability (GPT-4)

3. **Calendar Integration Issues**
   - Verify Google Calendar API credentials
   - Check calendar permissions and scopes
   - Test MCP server calendar endpoints directly

4. **AutoGen Import Errors**
   - Ensure AutoGen packages are installed correctly
   - Check Python environment and virtual environment
   - Verify AutoGen version compatibility

### Debug Logging

Enable debug logging for detailed troubleshooting:

```python
import logging
logging.getLogger("autogen_planner").setLevel(logging.DEBUG)
logging.getLogger("mcp_client").setLevel(logging.DEBUG)
```

### Health Checks

Monitor system health with built-in checks:

```python
# Check AutoGen agent status
agent = AutoGenPlannerAgent()
health = await agent.calendar_tool.list_calendar_events()
print(f"Calendar API: {'✅' if health['success'] else '❌'}")

# Check MCP server
mcp_status = await health_check_mcp()
print(f"MCP Server: {'✅' if mcp_status else '❌'}")
```

## Future Enhancements

### Planned Features

1. **Advanced AI Models**
   - Support for different OpenAI models (GPT-3.5, GPT-4, etc.)
   - Local model integration (Ollama, Hugging Face)
   - Model selection based on task complexity

2. **Enhanced Calendar Features**
   - Multiple calendar support
   - Calendar event updates and deletions
   - Recurring event handling
   - Meeting room booking integration

3. **Smart Scheduling**
   - Machine learning for optimal time slot prediction
   - Personal productivity pattern analysis
   - Team calendar coordination
   - Travel time consideration

4. **Advanced Notifications**
   - Intelligent reminder timing
   - Context-aware notifications
   - Integration with other productivity tools
   - Voice and video call reminders

### Contributing

To contribute to the AutoGen integration:

1. Fork the repository
2. Create a feature branch
3. Add comprehensive tests
4. Update documentation
5. Submit a pull request

See [Development Guide](../development/contributing.md) for detailed guidelines.

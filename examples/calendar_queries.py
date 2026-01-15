#!/usr/bin/env python3
"""
Quick reference: Using CalendarHaunter for Google Calendar queries.

This file shows copy-paste examples of the most common patterns.
"""

import asyncio
from datetime import date

from fateforger.agents.admonisher.calendar import CalendarHaunter

# ============================================================================
# PATTERN 1: Create and use CalendarHaunter directly
# ============================================================================


async def example_direct_usage():
    """Most basic usage pattern."""
    haunter = CalendarHaunter(
        session_id=123,
        slack=None,  # In real usage, pass actual Slack client
        scheduler=None,  # In real usage, pass scheduler
        channel="C123456",
    )

    # Simple queries
    today_events = await haunter.get_todays_events()
    print(f"📅 Today's events:\n{today_events}")

    week_schedule = await haunter.get_weekly_schedule()
    print(f"📅 Week's schedule:\n{week_schedule}")

    all_calendars = await haunter.list_calendars()
    print(f"📅 All calendars:\n{all_calendars}")


# ============================================================================
# PATTERN 2: Search for specific events
# ============================================================================


async def example_search_events():
    """Search calendar for events matching criteria."""
    haunter = CalendarHaunter(123, None, None, "C123456")

    # Search by keyword
    search_results = await haunter.search_events("meeting")
    print(f"Search results:\n{search_results}")

    # Search by person
    search_results = await haunter.search_events("John")
    print(f"Events with John:\n{search_results}")


# ============================================================================
# PATTERN 3: Ask arbitrary questions (most flexible)
# ============================================================================


async def example_natural_language_queries():
    """Use natural language for complex queries."""
    haunter = CalendarHaunter(123, None, None, "C123456")

    # Natural language questions
    questions = [
        "What meetings do I have tomorrow?",
        "How much free time do I have this week?",
        "What events do I have with external attendees?",
        "Show me all my 1-on-1s this month",
        "Which events are longer than 2 hours?",
        "Do I have any conflicts on Friday?",
    ]

    for question in questions:
        response = await haunter.ask_calendar_question(question)
        print(f"Q: {question}\nA: {response}\n")


# ============================================================================
# PATTERN 4: Create events (draft only - demonstrates capability)
# ============================================================================


async def example_create_event():
    """Create a new calendar event (through LLM instruction)."""
    haunter = CalendarHaunter(123, None, None, "C123456")

    # The LLM will interpret and execute this
    result = await haunter.create_event(
        title="Team Sync",
        start_time="2025-12-10 15:00",
        description="Weekly team synchronization",
    )
    print(f"Create result:\n{result}")


# ============================================================================
# PATTERN 5: Integration with bot handlers (recommended pattern)
# ============================================================================


async def example_bot_integration():
    """
    How to use CalendarHaunter in a Slack bot handler.
    This is the recommended pattern for production use.
    """

    # In your bot handler:
    class MyCalendarHandler:
        def __init__(self, slack_client, scheduler):
            self.slack = slack_client
            self.scheduler = scheduler

        async def handle_calendar_question(
            self, user_question: str, session_id: int, channel: str
        ) -> str:
            """Handle a user's calendar question."""

            # Create haunter for this session
            haunter = CalendarHaunter(
                session_id=session_id,
                slack=self.slack,
                scheduler=self.scheduler,
                channel=channel,
            )

            # Ask the question
            try:
                response = await haunter.ask_calendar_question(user_question)
                return response
            except Exception as e:
                return f"❌ Calendar error: {e}"


# ============================================================================
# PATTERN 6: Batch queries (process multiple questions)
# ============================================================================


async def example_batch_queries():
    """Process multiple calendar queries efficiently."""
    haunter = CalendarHaunter(123, None, None, "C123456")

    queries = [
        "What events do I have today?",
        "Show me my schedule for tomorrow",
        "List all recurring meetings",
        "Any free slots this afternoon?",
    ]

    # Run all in parallel
    results = await asyncio.gather(*[haunter.ask_calendar_question(q) for q in queries])

    for query, result in zip(queries, results):
        print(f"Q: {query}\nA: {result}\n")


# ============================================================================
# PATTERN 7: Using the utility function
# ============================================================================


async def example_create_agent_directly():
    """
    Lower-level: Create agent directly (for testing/advanced use).
    Usually you'd use CalendarHaunter class instead.
    """
    from fateforger.agents.admonisher.calendar import create_calendar_haunter_agent

    agent = await create_calendar_haunter_agent()

    from autogen_agentchat.messages import TextMessage

    response = await agent.on_messages(
        [TextMessage(content="What events do I have today?", source="user")]
    )

    print(f"Agent response: {response.chat_message.content}")


# ============================================================================
# PATTERN 8: Error handling (production-ready)
# ============================================================================


async def example_error_handling():
    """Robust error handling pattern."""
    haunter = CalendarHaunter(123, None, None, "C123456")

    try:
        # This might fail if MCP server is down
        events = await haunter.get_todays_events()
        print(f"✅ Success: {events}")

    except RuntimeError as e:
        # MCP server issues
        print(f"❌ Calendar service error: {e}")
        return None

    except Exception as e:
        # Unexpected errors
        print(f"❌ Unexpected error: {e}")
        return None

    return events


# ============================================================================
# MAIN: Run examples
# ============================================================================


async def main():
    """Run all examples (comment out as needed)."""

    print("=" * 70)
    print("PATTERN 1: Direct Usage")
    print("=" * 70)
    try:
        await example_direct_usage()
    except Exception as e:
        print(f"Note: {e} (MCP server may not be running)")

    print("\n" + "=" * 70)
    print("PATTERN 3: Natural Language Queries")
    print("=" * 70)
    try:
        await example_natural_language_queries()
    except Exception as e:
        print(f"Note: {e} (MCP server may not be running)")

    # Add more as needed


if __name__ == "__main__":
    # Check environment
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  OPENAI_API_KEY not set")

    mcp_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    print(f"📡 Using MCP server: {mcp_url}")

    # Run examples
    asyncio.run(main())

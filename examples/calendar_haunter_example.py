#!/usr/bin/env python3
"""
Example usage of the FateForger Calendar Haunter.

This script demonstrates how to use the CalendarHaunter with real Google Calendar data
via the AutoGen MCP integration.

Prerequisites:
1. Set OPENAI_API_KEY environment variable
2. Set MCP_CALENDAR_SERVER_URL environment variable (default: http://localhost:3000)
3. Have the Google Calendar MCP server running with OAuth credentials configured

Usage:
    python examples/calendar_haunter_example.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add fateforger to path for examples
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.admonisher.calendar import create_calendar_haunter_agent


async def main():
    """Demonstrate calendar haunter functionality."""
    print("🎯 FateForger Calendar Haunter Example")
    print("=" * 50)

    # Check prerequisites
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ Error: OPENAI_API_KEY environment variable not set")
        print("   Please set your OpenAI API key:")
        print("   export OPENAI_API_KEY='your-api-key-here'")
        return

    mcp_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    print(f"🔗 Using MCP server: {mcp_url}")
    print(f"🔑 OpenAI API key: {'*' * 20}{os.getenv('OPENAI_API_KEY', '')[-10:]}")
    print()

    try:
        # Create the calendar agent
        print("🚀 Creating AutoGen MCP calendar agent...")
        agent = await create_calendar_haunter_agent()
        print("✅ Calendar agent created successfully!")
        print(f"   Agent name: {agent.name}")
        print(f"   Agent model: GPT-4o-mini with real Google Calendar tools")
        print()

        # Import the necessary classes for querying
        from autogen_agentchat.messages import TextMessage
        from autogen_core import CancellationToken

        async def ask_agent(question: str) -> str:
            """Helper to ask the agent questions."""
            print(f"❓ Question: {question}")
            message = TextMessage(content=question, source="user")
            response = await agent.on_messages([message], CancellationToken())

            # Extract text from response
            content = getattr(
                response.chat_message, "content", str(response.chat_message)
            )
            if isinstance(content, list) and content:
                text_parts = [
                    item.get("text", str(item))
                    for item in content
                    if isinstance(item, dict)
                ]
                answer = "\n".join(text_parts) if text_parts else str(content[0])
            else:
                answer = str(content)

            print(f"🤖 Agent: {answer[:200]}{'...' if len(answer) > 200 else ''}")
            print()
            return answer

        # Demonstrate various calendar queries
        print("📅 Demonstrating calendar queries...")
        print("-" * 30)

        # Query 1: List available calendars
        await ask_agent("Can you list all my Google Calendar calendars?")

        # Query 2: Today's events
        from datetime import date

        today = date.today().isoformat()
        await ask_agent(f"What events do I have today ({today})?")

        # Query 3: This week's schedule
        await ask_agent("What's my schedule looking like this week?")

        # Query 4: Search for specific events
        await ask_agent("Search my calendar for any events containing 'meeting'")

        # Query 5: Free time analysis
        await ask_agent("Do I have any free time tomorrow afternoon?")

        print("🎉 Calendar haunter demonstration completed successfully!")
        print("\n💡 Tips for integration:")
        print("   • Use CalendarHaunter class for Slack bot integration")
        print("   • All methods return strings suitable for messaging")
        print("   • Agent handles natural language queries intelligently")
        print("   • HTTP transport bypasses broken SSE for reliability")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n🔧 Troubleshooting:")
        print("   • Ensure MCP calendar server is running")
        print("   • Check Google Calendar OAuth credentials")
        print("   • Verify network connectivity to MCP server")
        print("   • Check OpenAI API key is valid")

        if "mcp_server_tools" in str(e):
            print("   • MCP server may not be responding at the expected URL")
        if "OpenAI" in str(e):
            print("   • Check OpenAI API key and account credits")


if __name__ == "__main__":
    asyncio.run(main())

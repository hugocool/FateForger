"""
Planner Bot - Helps with task planning and scheduling.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .common import Config, logger, format_slack_message


class PlannerBot:
    """
    A Slack bot that helps users plan their tasks and schedule their day.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        if not self.config.validate():
            raise ValueError("Invalid configuration")

        self.app = App(
            token=self.config.slack_bot_token,
            signing_secret=self.config.slack_signing_secret,
        )

        self._register_handlers()

    def _register_handlers(self):
        """Register Slack event handlers."""

        @self.app.message("plan")
        def handle_plan_request(message, say):
            """Handle planning requests."""
            user = message.get("user")
            text = message.get("text", "")

            logger.info(f"Plan request from {user}: {text}")

            # Extract planning context from message
            planning_context = self._extract_planning_context(text)

            # Generate plan using AI
            plan = self._generate_plan(planning_context)

            # Send response
            response = format_slack_message(
                f"Here's your plan for today:\n\n{plan}", "PlannerBot"
            )
            say(response)

        @self.app.command("/plan")
        def handle_plan_command(ack, respond, command):
            """Handle /plan slash command."""
            ack()

            user_id = command["user_id"]
            text = command.get("text", "")

            logger.info(f"Plan command from {user_id}: {text}")

            if not text:
                respond(
                    "Please provide some context for your planning needs. Example: `/plan I have meetings at 2pm and 4pm, need to finish the report`"
                )
                return

            planning_context = self._extract_planning_context(text)
            plan = self._generate_plan(planning_context)

            respond(f"🗓️ **Your Plan**\n\n{plan}")

    def _extract_planning_context(self, text: str) -> Dict[str, Any]:
        """Extract planning context from user message."""
        # TODO: Implement AI-powered context extraction
        return {"raw_text": text, "tasks": [], "time_constraints": [], "priorities": []}

    def _generate_plan(self, context: Dict[str, Any]) -> str:
        """Generate a plan based on the context."""
        # TODO: Integrate with AutoGen for AI-powered planning
        raw_text = context.get("raw_text", "")

        if not raw_text:
            return "I need more information to create a plan. Please describe what you need to accomplish."

        # Basic planning logic for now
        return f"""
📋 **Today's Plan Based on**: {raw_text}

⏰ **Time Blocks:**
• 9:00 AM - Focus time for important tasks
• 11:00 AM - Check and respond to messages  
• 1:00 PM - Lunch break
• 2:00 PM - Scheduled meetings/calls
• 4:00 PM - Wrap up and planning for tomorrow

💡 **Suggestions:**
• Block calendar time for deep work
• Set phone to do-not-disturb during focus blocks
• Take breaks every 90 minutes
• Review and adjust plan as needed

Would you like me to help you break down any specific tasks?
        """.strip()

    def start(self):
        """Start the planner bot."""
        logger.info("Starting Planner Bot...")

        if not self.config.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")

        handler = SocketModeHandler(self.app, self.config.slack_app_token)
        handler.start()


def main():
    """Main entry point for the planner bot."""
    try:
        config = Config()
        bot = PlannerBot(config)
        bot.start()
    except KeyboardInterrupt:
        logger.info("Planner Bot stopped by user")
    except Exception as e:
        logger.error(f"Error starting Planner Bot: {e}")
        raise


if __name__ == "__main__":
    main()

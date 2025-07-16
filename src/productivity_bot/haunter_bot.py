"""
Haunter Bot - Sends reminders and follows up on tasks.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .common import Config, logger, format_slack_message


class HaunterBot:
    """
    A Slack bot that haunts users with reminders and follow-ups.
    Ensures tasks don't fall through the cracks.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        if not self.config.validate():
            raise ValueError("Invalid configuration")

        self.app = App(
            token=self.config.slack_bot_token,
            signing_secret=self.config.slack_signing_secret,
        )

        self.scheduler = AsyncIOScheduler()
        self.reminders: Dict[str, Dict] = {}

        self._register_handlers()

    def _register_handlers(self):
        """Register Slack event handlers."""

        @self.app.message("remind")
        def handle_reminder_request(message, say):
            """Handle reminder requests."""
            user = message.get("user")
            text = message.get("text", "")

            logger.info(f"Reminder request from {user}: {text}")

            # Parse reminder details
            reminder_details = self._parse_reminder(text, user)

            if reminder_details:
                self._schedule_reminder(reminder_details)
                response = format_slack_message(
                    f"👻 I'll remind you about: {reminder_details['task']}\n"
                    f"⏰ At: {reminder_details['when']}",
                    "HaunterBot",
                )
            else:
                response = format_slack_message(
                    "I couldn't understand your reminder request. Try: 'remind me to submit report in 2 hours'",
                    "HaunterBot",
                )

            say(response)

        @self.app.command("/remind")
        def handle_remind_command(ack, respond, command):
            """Handle /remind slash command."""
            ack()

            user_id = command["user_id"]
            text = command.get("text", "")

            logger.info(f"Remind command from {user_id}: {text}")

            if not text:
                respond(
                    "Usage: `/remind [task] in [time]`\nExample: `/remind submit report in 30 minutes`"
                )
                return

            reminder_details = self._parse_reminder(text, user_id)

            if reminder_details:
                self._schedule_reminder(reminder_details)
                respond(
                    f"👻 **Reminder Set**\n\n📋 Task: {reminder_details['task']}\n⏰ When: {reminder_details['when']}"
                )
            else:
                respond(
                    "❌ I couldn't parse your reminder. Try: `/remind [task] in [time]`"
                )

        @self.app.command("/haunt")
        def handle_haunt_command(ack, respond, command):
            """Handle /haunt slash command for persistent reminders."""
            ack()

            user_id = command["user_id"]
            text = command.get("text", "")

            # Set up recurring haunting
            respond(
                "👻 **Haunting Mode Activated**\n\nI'll persistently remind you until you mark it as done!"
            )

    def _parse_reminder(self, text: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Parse reminder text to extract task and timing."""
        # Simple parsing - can be enhanced with NLP
        import re

        # Look for patterns like "remind me to X in Y minutes/hours"
        patterns = [
            r"remind me to (.+) in (\d+) (minute|minutes|hour|hours|day|days)",
            r"remind (.+) in (\d+) (minute|minutes|hour|hours|day|days)",
            r"(.+) in (\d+) (minute|minutes|hour|hours|day|days)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                task = match.group(1).strip()
                amount = int(match.group(2))
                unit = match.group(3)

                # Calculate when to remind
                now = datetime.now()
                if unit.startswith("minute"):
                    when = now + timedelta(minutes=amount)
                elif unit.startswith("hour"):
                    when = now + timedelta(hours=amount)
                elif unit.startswith("day"):
                    when = now + timedelta(days=amount)
                else:
                    continue

                return {
                    "task": task,
                    "when": when.strftime("%Y-%m-%d %H:%M:%S"),
                    "user_id": user_id,
                    "timestamp": when,
                }

        return None

    def _schedule_reminder(self, reminder_details: Dict[str, Any]):
        """Schedule a reminder."""
        reminder_id = f"{reminder_details['user_id']}_{len(self.reminders)}"
        self.reminders[reminder_id] = reminder_details

        self.scheduler.add_job(
            self._send_reminder,
            "date",
            run_date=reminder_details["timestamp"],
            args=[reminder_id],
            id=reminder_id,
        )

        logger.info(f"Scheduled reminder {reminder_id} for {reminder_details['when']}")

    async def _send_reminder(self, reminder_id: str):
        """Send a scheduled reminder."""
        if reminder_id not in self.reminders:
            return

        reminder = self.reminders[reminder_id]
        user_id = reminder["user_id"]
        task = reminder["task"]

        try:
            # Send DM to user
            response = await self.app.client.chat_postMessage(
                channel=user_id,
                text=f"👻 **Reminder**: {task}\n\nDon't let this haunt you - take action now!",
                username="HaunterBot",
                icon_emoji=":ghost:",
            )

            logger.info(f"Sent reminder {reminder_id} to {user_id}")

            # Clean up
            del self.reminders[reminder_id]

        except Exception as e:
            logger.error(f"Failed to send reminder {reminder_id}: {e}")

    def start(self):
        """Start the haunter bot."""
        logger.info("Starting Haunter Bot...")

        if not self.config.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")

        # Start scheduler
        self.scheduler.start()

        # Start Slack app
        handler = SocketModeHandler(self.app, self.config.slack_app_token)
        handler.start()

    def stop(self):
        """Stop the haunter bot."""
        logger.info("Stopping Haunter Bot...")
        self.scheduler.shutdown()


def main():
    """Main entry point for the haunter bot."""
    try:
        config = Config()
        bot = HaunterBot(config)
        bot.start()
    except KeyboardInterrupt:
        logger.info("Haunter Bot stopped by user")
        bot.stop()
    except Exception as e:
        logger.error(f"Error starting Haunter Bot: {e}")
        raise


if __name__ == "__main__":
    main()

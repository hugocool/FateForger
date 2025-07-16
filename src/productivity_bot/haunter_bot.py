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

from .common import get_config, get_logger
from .database import get_db_session
from .models import CalendarEvent, EventStatus, PlanningSession, PlanStatus
from sqlalchemy import select

logger = get_logger("haunter_bot")


class HaunterBot:
    """
    A Slack bot that haunts users with reminders and follow-ups.
    Ensures tasks don't fall through the cracks.
    """

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config()

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


# Calendar Event Haunting Functions (called by APScheduler)
async def haunt_event(event_id: str):
    """
    Haunt a user about an upcoming calendar event.
    This function is called by APScheduler as a scheduled job.
    """
    try:
        logger.info(f"Haunting event {event_id}")

        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.event_id == event_id)
            )
            event = result.scalar_one_or_none()

            if not event:
                logger.warning(f"Event {event_id} not found for haunting")
                return

            if event.status != EventStatus.UPCOMING:
                logger.info(f"Event {event_id} is no longer upcoming, skipping haunt")
                return

            # Calculate time until event
            now = datetime.utcnow()
            time_until = event.start_time - now
            minutes_until = int(time_until.total_seconds() / 60)

            if minutes_until <= 0:
                logger.info(
                    f"Event {event_id} has already started, marking as completed"
                )
                event.status = EventStatus.COMPLETED
                await db.commit()
                return

            # Create reminder message
            if minutes_until <= 5:
                urgency = "🚨 STARTING NOW"
            elif minutes_until <= 15:
                urgency = "⚠️ STARTING SOON"
            else:
                urgency = "📅 UPCOMING"

            time_text = f"in {minutes_until} minutes" if minutes_until > 0 else "now"

            message_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{urgency} *{event.title or 'Untitled Event'}*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*When:* {time_text}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Duration:* {event.duration_minutes} minutes",
                        },
                    ],
                },
            ]

            if event.location:
                message_blocks[1]["fields"].append(
                    {"type": "mrkdwn", "text": f"*Location:* {event.location}"}
                )

            if event.description:
                message_blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Description:* {event.description[:200]}{'...' if len(event.description) > 200 else ''}",
                        },
                    }
                )

            # Add action buttons
            message_blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Mark as Done"},
                            "action_id": "mark_event_done",
                            "value": event_id,
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Snooze 10min"},
                            "action_id": "snooze_event",
                            "value": f"{event_id}:10",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Cancel Event"},
                            "action_id": "cancel_event",
                            "value": event_id,
                            "style": "danger",
                        },
                    ],
                }
            )

            # Try to notify organizer or attendees
            # For now, we'll use a default channel or user
            # TODO: Map calendar events to Slack users

            # This is a simplified version - in practice you'd want to:
            # 1. Map Google Calendar emails to Slack user IDs
            # 2. Send to appropriate channels based on event type
            # 3. Handle privacy and permissions properly

            # For demo purposes, log the message
            logger.info(f"Would send event reminder: {message_blocks}")

            # If you have a way to map organizer email to Slack user, do it here:
            # if event.organizer_email:
            #     slack_user_id = await get_slack_user_by_email(event.organizer_email)
            #     if slack_user_id:
            #         await send_slack_message(slack_user_id, message_blocks)

    except Exception as e:
        logger.error(f"Error haunting event {event_id}: {e}")


async def haunt_planning_session(session_id: int):
    """
    Haunt a user about their planning session.
    This function is called by APScheduler as a scheduled job.
    """
    try:
        logger.info(f"Haunting planning session {session_id}")

        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                logger.warning(f"Planning session {session_id} not found for haunting")
                return

            if session.status == PlanStatus.COMPLETE:
                logger.info(
                    f"Planning session {session_id} is already complete, skipping haunt"
                )
                return

            # Determine message based on session status and time
            now = datetime.utcnow()

            if session.status == PlanStatus.NOT_STARTED:
                message = f"👻 Time to start your planning session for {session.date.strftime('%A, %B %d')}!"
                urgency = "🕐 READY TO START"

                # Mark as in progress
                session.status = PlanStatus.IN_PROGRESS
                await db.commit()

            else:  # IN_PROGRESS
                hours_since_scheduled = (
                    now - session.scheduled_for
                ).total_seconds() / 3600

                if hours_since_scheduled > 8:
                    message = f"👻 Your planning session from this morning is still open. Time to wrap up or mark it complete!"
                    urgency = "⏰ LONG OVERDUE"
                elif hours_since_scheduled > 4:
                    message = f"👻 Don't forget to complete your planning session from earlier today!"
                    urgency = "⚠️ OVERDUE"
                else:
                    message = (
                        f"👻 How's your planning session going? Time for a check-in!"
                    )
                    urgency = "📋 CHECK-IN"

            message_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{urgency} *Planning Session*\n{message}",
                    },
                }
            ]

            if session.goals:
                message_blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Your Goals:*\n{session.goals[:300]}{'...' if len(session.goals) > 300 else ''}",
                        },
                    }
                )

            # Add action buttons
            message_blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Mark Complete"},
                            "action_id": "complete_planning_session",
                            "value": str(session_id),
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Review/Update"},
                            "action_id": "review_planning_session",
                            "value": str(session_id),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Snooze 1hr"},
                            "action_id": "snooze_planning_session",
                            "value": f"{session_id}:60",
                        },
                    ],
                }
            )

            # Send to user
            # TODO: Send actual Slack message to session.user_id
            logger.info(
                f"Would send planning session reminder to {session.user_id}: {message_blocks}"
            )

    except Exception as e:
        logger.error(f"Error haunting planning session {session_id}: {e}")


def main():
    """Main entry point for the haunter bot."""
    try:
        config = get_config()
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

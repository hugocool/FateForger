"""
Planner Bot - AI-powered planning and task scheduling bot.

This module provides an interactive Slack bot that helps users plan their daily
tasks and goals through conversational interfaces and modal forms. The bot
integrates with the database to persist planning sessions and provides
follow-up reminders.

Key Features:
    - Interactive daily planning modals
    - Goal setting and time-boxing support
    - Session persistence and retrieval
    - Follow-up reminders and check-ins
    - Integration with the haunter bot for accountability

Example:
    ```python
    from productivity_bot.planner_bot import PlannerBot
    
    # Initialize the bot
    bot = PlannerBot()
    
    # Start the bot service
    await bot.run()
    ```
"""

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, Optional

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from .common import get_config, get_logger
from .database import PlanningSessionService, get_db_session
from .models import PlanStatus

logger = get_logger("planner_bot")


class PlannerBot:
    """
    A Slack bot that helps users plan their day using interactive modals.

    The PlannerBot provides an intuitive interface for daily planning through
    Slack modals, allowing users to set goals, create time-boxes, and track
    their progress throughout the day.

    Attributes:
        config: Configuration object containing bot tokens and settings.
        app: AsyncApp instance for handling Slack interactions.

    Example:
        >>> config = get_config()
        >>> bot = PlannerBot(config)
        >>> await bot.run()
    """

    def __init__(self, config: Optional[Any] = None) -> None:
        """
        Initialize the PlannerBot with configuration.

        Args:
            config: Optional configuration object. If None, will use default config.
        """
        self.config = config or get_config()

        # Initialize Slack app
        self.app = AsyncApp(
            token=self.config.slack_bot_token,
            signing_secret=self.config.slack_signing_secret,
        )

        self._register_handlers()

    def _build_planning_modal_view(
        self, session_date: date, initial_goals: str = "", initial_timebox: str = ""
    ) -> Dict[str, Any]:
        """
        Build the planning modal view structure.

        Args:
            session_date: The date for the planning session.
            initial_goals: Pre-filled goals text.
            initial_timebox: Pre-filled timebox text.

        Returns:
            Dictionary containing the Slack modal view structure.
        """
        return {
            "type": "modal",
            "callback_id": "plan_today_view",
            "title": {"type": "plain_text", "text": "Daily Planning"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Planning for {session_date.strftime('%A, %B %d, %Y')}*",
                    },
                },
                {"type": "divider"},
                {
                    "type": "input",
                    "block_id": "goals",
                    "label": {
                        "type": "plain_text",
                        "text": "Top 3 goals for today",
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": "What are the most important things you want to accomplish?",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "multiline": True,
                        "initial_value": initial_goals,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "1. Complete project review\n2. Prepare presentation\n3. Team meeting follow-up",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "timebox",
                    "label": {"type": "plain_text", "text": "Time-box summary"},
                    "hint": {
                        "type": "plain_text",
                        "text": "How will you structure your day? Include breaks and buffer time.",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "multiline": True,
                        "initial_value": initial_timebox,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "9:00-10:30 Deep work\n10:30-11:00 Break\n11:00-12:00 Meetings\n14:00-16:00 Project work",
                        },
                    },
                },
            ],
        }

    def _register_handlers(self) -> None:
        """
        Register Slack event handlers.

        Sets up all the command handlers, view submissions, and button actions
        that the bot responds to in Slack.
        """

        @self.app.command("/plan-today")
        async def open_plan_modal(ack, body, client):
            """
            Open the daily planning modal.

            Displays an interactive modal for users to set their daily goals
            and create a time-boxed schedule for the day.
            """
            await ack()

            user_id = body["user_id"]
            today = date.today()

            logger.info(f"Opening plan modal for user {user_id}")

            # Check if user already has a planning session for today
            existing_session = await PlanningSessionService.get_user_session_for_date(
                user_id, today
            )

            # Pre-populate modal if session exists
            initial_goals = ""
            initial_timebox = ""

            if existing_session:
                initial_goals = existing_session.goals or ""
                initial_timebox = existing_session.notes or ""

            await client.views_open(
                trigger_id=body["trigger_id"],
                view=self._build_planning_modal_view(
                    today, initial_goals, initial_timebox
                ),
            )

        @self.app.view("plan_today_view")
        async def save_plan(ack, body, view, logger):
            """Save the daily planning session."""
            await ack()

            user_id = body["user"]["id"]
            today = date.today()

            # Extract form values
            goals_value = view["state"]["values"]["goals"]["plain_text_input-action"][
                "value"
            ]
            timebox_value = view["state"]["values"]["timebox"][
                "plain_text_input-action"
            ]["value"]

            logger.info(
                f"Saving plan for user {user_id} - Goals: {len(goals_value)} chars, Timebox: {len(timebox_value)} chars"
            )

            try:
                # Check if session already exists
                existing_session = (
                    await PlanningSessionService.get_user_session_for_date(
                        user_id, today
                    )
                )

                if existing_session:
                    # Update existing session
                    existing_session.goals = goals_value
                    existing_session.notes = timebox_value
                    existing_session.status = PlanStatus.IN_PROGRESS

                    async with get_db_session() as db:
                        db.add(existing_session)
                        # Note: commit is handled automatically by the context manager

                    logger.info(
                        f"Updated existing planning session {existing_session.id}"
                    )
                    session_id = existing_session.id

                else:
                    # Create new session
                    scheduled_for = datetime.combine(
                        today, datetime.min.time().replace(hour=9)
                    )

                    session = await PlanningSessionService.create_session(
                        user_id=user_id,
                        session_date=today,
                        scheduled_for=scheduled_for,
                        goals=goals_value,
                    )

                    # Update with timebox info and set to in progress
                    await PlanningSessionService.add_session_notes(
                        session.id, timebox_value
                    )
                    await PlanningSessionService.update_session_status(
                        session.id, PlanStatus.IN_PROGRESS
                    )

                    logger.info(f"Created new planning session {session.id}")
                    session_id = session.id

                # Send confirmation message
                await self.app.client.chat_postMessage(
                    channel=user_id,
                    text="✅ Your daily plan has been saved!",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Daily Plan Saved* 📋\n\nYour plan for {today.strftime('%A, %B %d')} is ready!",
                            },
                        },
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Goals:*\n{goals_value[:200]}{'...' if len(goals_value) > 200 else ''}",
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Schedule:*\n{timebox_value[:200]}{'...' if len(timebox_value) > 200 else ''}",
                                },
                            ],
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {
                                        "type": "plain_text",
                                        "text": "View Full Plan",
                                    },
                                    "action_id": "view_plan",
                                    "value": str(session_id),
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Edit Plan"},
                                    "action_id": "edit_plan",
                                    "value": str(session_id),
                                },
                            ],
                        },
                    ],
                )

                # TODO: Schedule follow-up reminder
                # await self._schedule_followup_reminder(user_id, session_id)

            except Exception as e:
                logger.error(f"Error saving plan for user {user_id}: {e}")

                # Send error message
                await self.app.client.chat_postMessage(
                    channel=user_id,
                    text="❌ Sorry, there was an error saving your plan. Please try again.",
                )

        @self.app.action("view_plan")
        async def view_full_plan(ack, body, client):
            """Show the full planning session details."""
            await ack()

            session_id = int(body["actions"][0]["value"])
            user_id = body["user"]["id"]

            session = await PlanningSessionService.get_session_by_id(session_id)

            if not session:
                await client.chat_postEphemeral(
                    channel=body["channel"]["id"],
                    user=user_id,
                    text="❌ Planning session not found.",
                )
                return

            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text="📋 Your Daily Plan",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Daily Plan for {session.date.strftime('%A, %B %d, %Y')}*\n*Status:* {session.status.value.replace('_', ' ').title()}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Goals:*\n{session.goals or 'No goals set'}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Schedule:*\n{session.notes or 'No schedule set'}",
                        },
                    },
                ],
            )

        @self.app.action("edit_plan")
        async def edit_plan(ack, body, client):
            """
            Re-open the planning modal for editing.

            Triggers the same modal interface as the /plan-today command
            to allow users to modify their existing planning session.
            """
            await ack()

            # Recreate the modal opening logic
            user_id = body["user"]["id"]
            today = date.today()

            logger.info(f"Opening plan modal for editing by user {user_id}")

            # Check if user already has a planning session for today
            existing_session = await PlanningSessionService.get_user_session_for_date(
                user_id, today
            )

            # Pre-populate modal if session exists
            initial_goals = ""
            initial_timebox = ""

            if existing_session:
                initial_goals = existing_session.goals or ""
                initial_timebox = existing_session.notes or ""

            await client.views_open(
                trigger_id=body["trigger_id"],
                view=self._build_planning_modal_view(
                    today, initial_goals, initial_timebox
                ),
            )

        @self.app.command("/plan-status")
        async def check_plan_status(ack, body, client):
            """Check the current planning session status."""
            await ack()

            user_id = body["user_id"]
            today = date.today()

            session = await PlanningSessionService.get_user_session_for_date(
                user_id, today
            )

            if not session:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text="📋 No planning session found for today. Use `/plan-today` to create one!",
                )
                return

            status_emoji = {
                PlanStatus.NOT_STARTED: "⏳",
                PlanStatus.IN_PROGRESS: "🏃",
                PlanStatus.COMPLETE: "✅",
            }

            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text=f"{status_emoji.get(session.status, '📋')} Your planning session status: *{session.status.value.replace('_', ' ').title()}*",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Planning Session for {today.strftime('%A, %B %d')}*\n*Status:* {session.status.value.replace('_', ' ').title()}",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "View Plan"},
                                "action_id": "view_plan",
                                "value": str(session.id),
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Edit Plan"},
                                "action_id": "edit_plan",
                                "value": str(session.id),
                            },
                        ],
                    },
                ],
            )

    async def start(self) -> None:
        """
        Start the planner bot.

        Initializes the Socket Mode handler and starts listening for Slack events.

        Raises:
            ValueError: If SLACK_APP_TOKEN is not configured.
            Exception: If bot fails to start.
        """
        logger.info("Starting Planner Bot...")

        if not self.config.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required for Socket Mode")

        handler = AsyncSocketModeHandler(self.app, self.config.slack_app_token)
        await handler.start_async()

    async def run(self) -> None:
        """
        Run the planner bot.

        Convenience method that calls start(). Included for compatibility
        with the documented interface.

        Raises:
            ValueError: If SLACK_APP_TOKEN is not configured.
            Exception: If bot fails to start.
        """
        await self.start()

    async def stop(self) -> None:
        """
        Stop the planner bot.

        Performs any necessary cleanup when shutting down the bot.
        """
        logger.info("Stopping Planner Bot...")
        # Any cleanup logic here


async def main() -> None:
    """
    Main entry point for the planner bot.

    Initializes and starts the PlannerBot service, handling graceful
    shutdown on keyboard interrupt.

    Raises:
        Exception: If bot initialization or startup fails.
    """
    try:
        config = get_config()
        bot = PlannerBot(config)
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Planner Bot stopped by user")
    except Exception as e:
        logger.error(f"Error starting Planner Bot: {e}")
        raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

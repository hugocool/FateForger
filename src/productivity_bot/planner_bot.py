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
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from .autogen_planner import AutoGenPlannerAgent
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

        # Initialize AutoGen planner agent
        self.autogen_agent = AutoGenPlannerAgent()

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

    def _build_reflection_modal_view(self, session) -> Dict[str, Any]:
        """
        Build the reflection modal view for session completion.

        Args:
            session: The planning session to reflect on.

        Returns:
            Dictionary representing the Slack modal view.
        """
        return {
            "type": "modal",
            "callback_id": "reflection_view",
            "title": {"type": "plain_text", "text": "Day Reflection"},
            "submit": {"type": "plain_text", "text": "Complete Session"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": str(session.id),
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Reflecting on {session.date.strftime('%A, %B %d, %Y')}*\n\nLet's wrap up your planning session with a quick reflection.",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Your Goals Today:*\n{session.goals or 'No goals set'}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "input",
                    "block_id": "accomplishments",
                    "label": {
                        "type": "plain_text",
                        "text": "What did you accomplish?",
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": "Share your wins, completed tasks, and progress made.",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "• Completed project review\n• Attended 3 meetings\n• Made progress on presentation",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "challenges",
                    "label": {
                        "type": "plain_text",
                        "text": "What challenges did you face?",
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": "What obstacles or difficulties came up during the day?",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "• Unexpected urgent tasks\n• Meeting ran over time\n• Technical issues",
                        },
                    },
                    "optional": True,
                },
                {
                    "type": "input",
                    "block_id": "lessons",
                    "label": {
                        "type": "plain_text",
                        "text": "What did you learn?",
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": "Any insights or lessons for better planning tomorrow?",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "• Need more buffer time between meetings\n• Morning deep work is most productive\n• Should schedule breaks more regularly",
                        },
                    },
                    "optional": True,
                },
                {
                    "type": "input",
                    "block_id": "energy_rating",
                    "label": {
                        "type": "plain_text",
                        "text": "How was your energy level today?",
                    },
                    "element": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Rate your energy level",
                        },
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "⚡ High Energy - Felt great all day",
                                },
                                "value": "high",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "🔋 Good Energy - Productive and focused",
                                },
                                "value": "good",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "😐 Medium Energy - Average day",
                                },
                                "value": "medium",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "😴 Low Energy - Struggled to focus",
                                },
                                "value": "low",
                            },
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "😵 Exhausted - Very difficult day",
                                },
                                "value": "exhausted",
                            },
                        ],
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
                    # Update existing session with goals and notes (preserve existing behavior for editing)
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
                    # 1️⃣ Create minimal planning session metadata
                    #    (no goals/notes stored here)
                    scheduled_for = datetime.combine(
                        today, time(hour=17, minute=0), timezone.utc
                    )
                    session = await PlanningSessionService.create_session(
                        user_id=user_id, session_date=today, scheduled_for=scheduled_for
                    )

                    session_id = session.id

                    # 2️⃣ Immediately schedule first haunt at the planning start time
                    from .scheduler import schedule_planning_session_haunt

                    job_id = schedule_planning_session_haunt(session_id, scheduled_for)
                    # Persist the scheduler job ID for future cancellation
                    await PlanningSessionService.update_session_job_id(
                        session_id, job_id
                    )

                    logger.info(
                        f"Created new planning session {session_id} with job {job_id}"
                    )

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

                # Only enhance with AI for new sessions (goals/notes will be added later via a different flow)
                if not existing_session:
                    await self._enhance_plan_with_autogen(
                        session_id, goals_value, today
                    )

            except Exception as e:
                logger.error(f"Error saving plan for user {user_id}: {e}")

                # Send error message
                await self.app.client.chat_postMessage(
                    channel=user_id,
                    text="❌ Sorry, there was an error saving your plan. Please try again.",
                )

        @self.app.view("reflection_view")
        async def save_reflection(ack, body, view, logger):
            """Save the reflection and mark session as complete."""
            await ack()

            user_id = body["user"]["id"]
            session_id = int(view["private_metadata"])

            # Extract reflection values
            accomplishments = view["state"]["values"]["accomplishments"][
                "plain_text_input-action"
            ]["value"]

            challenges_input = view["state"]["values"]["challenges"][
                "plain_text_input-action"
            ]
            challenges = challenges_input["value"] if challenges_input["value"] else ""

            lessons_input = view["state"]["values"]["lessons"][
                "plain_text_input-action"
            ]
            lessons = lessons_input["value"] if lessons_input["value"] else ""

            energy_level = view["state"]["values"]["energy_rating"][
                "static_select-action"
            ]["selected_option"]["value"]

            logger.info(f"Saving reflection for session {session_id} by user {user_id}")

            try:
                # Get the session
                session = await PlanningSessionService.get_session_by_id(session_id)
                if not session:
                    await self.app.client.chat_postMessage(
                        channel=user_id,
                        text="❌ Planning session not found.",
                    )
                    return

                # Build reflection summary
                reflection_summary = f"""
## Day Reflection - {session.date.strftime('%A, %B %d, %Y')}

### Accomplishments:
{accomplishments}

### Challenges:
{challenges if challenges else 'None noted'}

### Lessons Learned:
{lessons if lessons else 'None noted'}

### Energy Level: {energy_level.title()}
"""

                # Update session with reflection and mark complete
                current_notes = session.notes or ""
                updated_notes = current_notes + "\n\n" + reflection_summary

                await PlanningSessionService.add_session_notes(
                    session_id, updated_notes
                )
                await PlanningSessionService.update_session_status(
                    session_id, PlanStatus.COMPLETE
                )

                # Cancel any pending haunter jobs
                await self._cancel_haunter_reminders(session_id)

                # Send completion confirmation
                energy_emoji = {
                    "high": "⚡",
                    "good": "🔋",
                    "medium": "😐",
                    "low": "😴",
                    "exhausted": "😵",
                }

                await self.app.client.chat_postMessage(
                    channel=user_id,
                    text="✅ Planning session completed!",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"🎉 *Planning Session Complete!*\n\nGreat work finishing your day with reflection on {session.date.strftime('%A, %B %d')}!",
                            },
                        },
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Accomplishments:*\n{accomplishments[:100]}{'...' if len(accomplishments) > 100 else ''}",
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Energy Level:*\n{energy_emoji.get(energy_level, '📊')} {energy_level.title()}",
                                },
                            ],
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Use `/plan-today` tomorrow to start planning your next productive day! 🚀",
                            },
                        },
                    ],
                )

                logger.info(f"Completed planning session {session_id} with reflection")

            except Exception as e:
                logger.error(f"Error saving reflection for session {session_id}: {e}")
                await self.app.client.chat_postMessage(
                    channel=user_id,
                    text="❌ Sorry, there was an error saving your reflection. Please try again.",
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

            # Build action buttons based on status
            action_elements = [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Plan"},
                    "action_id": "view_plan",
                    "value": str(session.id),
                }
            ]

            # Add appropriate action buttons based on status
            if session.status != PlanStatus.COMPLETE:
                action_elements.extend(
                    [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Edit Plan"},
                            "action_id": "edit_plan",
                            "value": str(session.id),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Mark Complete"},
                            "action_id": "complete_planning_session",
                            "value": str(session.id),
                            "style": "primary",
                        },
                    ]
                )

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
                        "elements": action_elements,
                    },
                ],
            )

        @self.app.command("/plan-complete")
        async def plan_complete_command(ack, body, client):
            """Mark the current planning session as complete with reflection."""
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
                    text="📋 No planning session found for today. Use `/plan-today` to create one first!",
                )
                return

            if session.status == PlanStatus.COMPLETE:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=user_id,
                    text="✅ Your planning session is already marked as complete!",
                )
                return

            # Open reflection modal
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=self._build_reflection_modal_view(session),
            )

        @self.app.action("complete_planning_session")
        async def complete_planning_session_action(ack, body, client):
            """Handle completion of planning session from haunter bot buttons."""
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

            if session.status == PlanStatus.COMPLETE:
                await client.chat_postEphemeral(
                    channel=body["channel"]["id"],
                    user=user_id,
                    text="✅ This planning session is already complete!",
                )
                return

            # Open reflection modal
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=self._build_reflection_modal_view(session),
            )

    async def _schedule_followup_reminder(self, user_id: str, session_id: int) -> None:
        """
        Schedule haunter follow-up reminders for a planning session.

        Args:
            user_id: Slack user ID.
            session_id: Planning session ID.
        """
        try:
            # Import here to avoid circular imports
            from .scheduler import schedule_haunt

            # Schedule first haunt in 1 hour
            first_reminder = datetime.utcnow() + timedelta(hours=1)

            job_id = schedule_haunt(session_id, first_reminder, attempt=1)

            # Update the session with the job ID
            async with get_db_session() as db:
                session = await PlanningSessionService.get_session_by_id(session_id)
                if session:
                    session.scheduler_job_id = job_id
                    session.next_nudge_attempt = 1
                    db.add(session)
                    # Commit handled by context manager

            logger.info(f"Scheduled first haunt for session {session_id} in 1 hour")

        except Exception as e:
            logger.error(
                f"Failed to schedule follow-up reminder for session {session_id}: {e}"
            )

    async def _cancel_haunter_reminders(self, session_id: int) -> None:
        """
        Cancel any pending haunter reminders for a planning session.

        Args:
            session_id: Planning session ID.
        """
        try:
            from .scheduler import cancel_planning_session_haunt

            # Cancel the haunt job for this session
            success = cancel_planning_session_haunt(session_id)
            if success:
                logger.info(f"Cancelled haunter job for session {session_id}")
            else:
                logger.info(
                    f"No haunter job found for session {session_id} (may have already completed)"
                )

        except Exception as e:
            logger.error(
                f"Failed to cancel haunter reminders for session {session_id}: {e}"
            )

    async def _enhance_plan_with_autogen(
        self, session_id: int, goals: str, plan_date: date
    ) -> None:
        """
        Enhance the planning session with AutoGen AI analysis.

        Args:
            session_id: Planning session ID.
            goals: User's goals for the day.
            plan_date: Date of the planning session.
        """
        try:
            # Get the session
            session = await PlanningSessionService.get_session_by_id(session_id)
            if not session:
                logger.warning(
                    f"Session {session_id} not found for AutoGen enhancement"
                )
                return

            # Generate enhanced plan using AutoGen
            enhanced_plan = await self.autogen_agent.generate_daily_plan(
                user_id=session.user_id,
                goals=goals,
                date_str=plan_date.strftime("%Y-%m-%d"),
            )

            if enhanced_plan.get("success"):
                # Store AI suggestions in the session notes (append to existing)
                ai_suggestions = enhanced_plan.get("raw_plan", "")

                current_notes = session.notes or ""
                enhanced_notes = (
                    current_notes + "\n\n## AI Suggestions:\n" + ai_suggestions
                )

                # Update session with enhanced notes
                await PlanningSessionService.add_session_notes(
                    session_id, enhanced_notes
                )

                logger.info(f"Enhanced session {session_id} with AutoGen suggestions")

                # Send AI suggestions to user as follow-up message
                await self._send_ai_enhancement_message(
                    session.user_id, session_id, enhanced_plan
                )
            else:
                logger.warning(
                    f"AutoGen enhancement failed for session {session_id}: {enhanced_plan.get('error')}"
                )

        except Exception as e:
            logger.error(
                f"Failed to enhance plan with AutoGen for session {session_id}: {e}"
            )

    async def _send_ai_enhancement_message(
        self, user_id: str, session_id: int, enhanced_plan: Dict[str, Any]
    ) -> None:
        """
        Send AI enhancement suggestions to the user.

        Args:
            user_id: Slack user ID.
            session_id: Planning session ID.
            enhanced_plan: Enhanced plan data from AutoGen.
        """
        try:
            structured_plan = enhanced_plan.get("structured_plan", {})
            recommendations = structured_plan.get("recommendations", [])
            schedule_items = structured_plan.get("schedule_items", [])

            # Build message blocks
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🤖 *AI Planning Assistant*\n\nI've analyzed your calendar and goals. Here are some optimization suggestions:",
                    },
                }
            ]

            # Add schedule suggestions if available
            if schedule_items:
                schedule_text = "\n".join(schedule_items[:5])  # Limit to 5 items
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*📅 Suggested Schedule:*\n```{schedule_text}```",
                        },
                    }
                )

            # Add recommendations if available
            if recommendations:
                rec_text = "\n".join([f"• {rec}" for rec in recommendations[:5]])
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*💡 Recommendations:*\n{rec_text}",
                        },
                    }
                )

            # Add action buttons
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Apply Suggestions"},
                            "action_id": "apply_ai_suggestions",
                            "value": str(session_id),
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Full Analysis",
                            },
                            "action_id": "view_ai_analysis",
                            "value": str(session_id),
                        },
                    ],
                }
            )

            await self.app.client.chat_postMessage(
                channel=user_id,
                text="🤖 AI planning suggestions are ready!",
                blocks=blocks,
            )

        except Exception as e:
            logger.error(f"Failed to send AI enhancement message: {e}")

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

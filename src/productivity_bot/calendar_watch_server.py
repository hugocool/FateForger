"""
Calendar Watch Server - FastAPI server for calendar webhooks and monitoring.
"""

# TODO: should this not all be replaced by the MCP server?
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from .common import get_config, get_logger, list_events_since
from .database import get_db_session
from .models import (
    CalendarEvent,
    CalendarSync,
    EventStatus,
    PlanningSession,
    PlanStatus,
)
from .scheduler import (
    get_scheduler,
    reschedule_haunt,
    schedule_event_haunt,
    start_scheduler,
)

logger = get_logger("calendar_watch_server")


class CalendarWatchServer:
    """
    FastAPI server that watches calendar events and integrates with Slack.
    Receives webhooks from calendar services and triggers appropriate actions.
    """

    def __init__(self, config: Optional[Any] = None):
        self.config = config or get_config()

        self.app = FastAPI(
            title="Productivity Bot Calendar Watcher",
            description="Watches calendar events and triggers productivity actions",
            version="0.1.0",
        )

        self._setup_routes()

    def _setup_routes(self):
        """Setup FastAPI routes."""

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}

        @self.app.post("/webhook/calendar")
        async def calendar_webhook(request: Request, background_tasks: BackgroundTasks):
            """Handle calendar webhook events."""
            try:
                data = await request.json()
                logger.info(f"Received calendar webhook: {data}")

                # Process webhook in background
                background_tasks.add_task(self._process_calendar_sync, data)

                return {"status": "received"}

            except Exception as e:
                logger.error(f"Error processing calendar webhook: {e}")
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.post("/webhook/google-calendar")
        async def google_calendar_webhook(
            request: Request, background_tasks: BackgroundTasks
        ):
            """Handle Google Calendar webhook notifications."""
            try:
                # Google Calendar sends notifications as headers
                channel_id = request.headers.get("x-goog-channel-id")
                resource_id = request.headers.get("x-goog-resource-id")
                resource_state = request.headers.get("x-goog-resource-state")

                logger.info(
                    f"Google Calendar notification - Channel: {channel_id}, State: {resource_state}, Resource: {resource_id}"
                )

                if resource_state == "sync":
                    return {"status": "sync_acknowledged"}

                # Process the calendar change in background
                webhook_data = {
                    "source": "google_calendar",
                    "channel_id": channel_id,
                    "resource_id": resource_id,
                    "state": resource_state,
                    "timestamp": datetime.now().isoformat(),
                }

                background_tasks.add_task(self._process_calendar_sync, webhook_data)

                return {"status": "processed"}

            except Exception as e:
                logger.error(f"Error processing Google Calendar webhook: {e}")
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/api/events/upcoming")
        async def get_upcoming_events():
            """Get upcoming calendar events for the next 7 days."""
            try:
                # Calculate date range
                now = datetime.utcnow()
                end_date = now + timedelta(days=7)

                async with get_db_session() as db:
                    result = await db.execute(
                        select(CalendarEvent)
                        .where(
                            CalendarEvent.start_time >= now,
                            CalendarEvent.start_time <= end_date,
                            CalendarEvent.status == EventStatus.UPCOMING,
                        )
                        .order_by(CalendarEvent.start_time)
                    )
                    events = result.scalars().all()

                    return {
                        "events": [
                            {
                                "event_id": event.event_id,
                                "title": event.title,
                                "description": event.description,
                                "start_time": event.start_time.isoformat(),
                                "end_time": event.end_time.isoformat(),
                                "location": event.location,
                                "duration_minutes": event.duration_minutes,
                                "status": event.status.value,
                                "organizer_email": event.organizer_email,
                                "attendees_count": event.attendees_count,
                            }
                            for event in events
                        ],
                        "count": len(events),
                        "date_range": {
                            "start": now.isoformat(),
                            "end": end_date.isoformat(),
                        },
                    }

            except Exception as e:
                logger.error(f"Error getting upcoming events: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/reminders")
        async def create_reminder(request: Request):
            """Create a new reminder."""
            try:
                data = await request.json()
                # TODO: Integrate with HaunterBot
                return {"status": "reminder_created", "data": data}
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

    async def _process_calendar_sync(self, webhook_data: Dict[str, Any]):
        """Process calendar synchronization from webhook."""
        try:
            resource_id = webhook_data.get("resource_id")
            state = webhook_data.get("state")

            logger.info(
                f"Processing calendar sync for resource {resource_id}, state: {state}"
            )

            if state == "exists":
                # Calendar was updated - sync all events
                if resource_id:
                    await self._sync_calendar_events(resource_id)

        except Exception as e:
            logger.error(f"Error processing calendar sync: {e}")

    async def _sync_calendar_events(self, calendar_id: str):
        """Synchronize all events for a calendar."""
        try:
            # Get sync state from database
            async with get_db_session() as db:
                result = db.execute(
                    select(CalendarSync).where(CalendarSync.calendar_id == calendar_id)
                )
                sync_record = result.scalar_one_or_none()

                sync_token = sync_record.sync_token if sync_record else None

                logger.info(
                    f"Syncing calendar {calendar_id} with sync_token: {sync_token}"
                )

                # Fetch events from MCP
                events_response = await list_events_since(calendar_id, sync_token)
                events = events_response.get("events", [])
                next_sync_token = events_response.get("nextSyncToken")

                logger.info(f"Retrieved {len(events)} events from MCP")

                # Process each event
                for event_data in events:
                    await self._upsert_calendar_event(event_data)

                # Update sync state
                if sync_record:
                    sync_record.sync_token = next_sync_token
                    sync_record.last_sync_at = datetime.utcnow()
                    sync_record.last_successful_sync_at = datetime.utcnow()
                    sync_record.sync_error = None
                else:
                    # Create new sync record
                    new_sync = CalendarSync(
                        calendar_id=calendar_id,
                        resource_id=calendar_id,  # For now, same as calendar_id
                        sync_token=next_sync_token,
                        last_sync_at=datetime.utcnow(),
                        last_successful_sync_at=datetime.utcnow(),
                    )
                    db.add(new_sync)

                logger.info(f"Calendar sync completed for {calendar_id}")

        except Exception as e:
            logger.error(f"Error syncing calendar events: {e}")

            # Update sync record with error
            if sync_record:
                async with get_db_session() as db:
                    sync_record.sync_error = str(e)
                    sync_record.last_sync_at = datetime.utcnow()
                    db.commit()

    async def _upsert_calendar_event(self, event_data: Dict[str, Any]) -> None:
        """Upsert a calendar event and handle moves/deletes with scheduler synchronization."""
        try:
            event_id = event_data.get("id")
            if not event_id:
                logger.warning("Event missing ID, skipping")
                return

            # Parse event data
            start_time = self._parse_datetime(event_data.get("start", {}))
            end_time = self._parse_datetime(event_data.get("end", {}))

            if not start_time or not end_time:
                logger.warning(f"Event {event_id} missing start/end time, skipping")
                return

            # Determine event status
            google_status = event_data.get("status", "confirmed")
            if google_status == "cancelled":
                event_status = EventStatus.CANCELLED
            elif end_time < datetime.utcnow():
                event_status = EventStatus.COMPLETED
            else:
                event_status = EventStatus.UPCOMING

            async with get_db_session() as db:
                # Check if event exists
                result = db.execute(
                    select(CalendarEvent).where(CalendarEvent.event_id == event_id)
                )
                existing_event = result.scalar_one_or_none()

                # Track time changes for move detection
                time_changed = False
                was_cancelled = False

                if existing_event:
                    # Detect event moves (time changes)
                    if (
                        existing_event.start_time != start_time
                        or existing_event.end_time != end_time
                    ):
                        time_changed = True
                        logger.info(
                            f"Event {event_id} moved: {existing_event.start_time} -> {start_time}"
                        )

                    # Detect cancellations
                    if (
                        existing_event.status != EventStatus.CANCELLED
                        and event_status == EventStatus.CANCELLED
                    ):
                        was_cancelled = True
                        logger.info(f"Event {event_id} was cancelled")

                    # Update existing event
                    existing_event.title = event_data.get("summary", "")
                    existing_event.description = event_data.get("description", "")
                    existing_event.location = event_data.get("location", "")
                    existing_event.start_time = start_time
                    existing_event.end_time = end_time
                    existing_event.status = event_status
                    existing_event.google_status = google_status
                    existing_event.google_updated = self._parse_datetime(
                        {"dateTime": event_data.get("updated")}
                    )
                    existing_event.organizer_email = event_data.get(
                        "organizer", {}
                    ).get("email")
                    existing_event.attendees_count = len(
                        event_data.get("attendees", [])
                    )
                    existing_event.last_synced_at = datetime.utcnow()

                    logger.info(f"Updated existing event {event_id}")
                    calendar_event = existing_event

                else:
                    # Create new event
                    calendar_event = CalendarEvent(
                        event_id=event_id,
                        calendar_id=event_data.get("calendarId", "primary"),
                        title=event_data.get("summary", ""),
                        description=event_data.get("description", ""),
                        location=event_data.get("location", ""),
                        start_time=start_time,
                        end_time=end_time,
                        status=event_status,
                        google_status=google_status,
                        google_updated=self._parse_datetime(
                            {"dateTime": event_data.get("updated")}
                        ),
                        organizer_email=event_data.get("organizer", {}).get("email"),
                        attendees_count=len(event_data.get("attendees", [])),
                        last_synced_at=datetime.utcnow(),
                    )

                    db.add(calendar_event)
                    logger.info(f"Created new event {event_id}")

                # Handle scheduler synchronization
                await self._sync_scheduler_for_event(
                    calendar_event, time_changed, was_cancelled
                )

                # Update related planning sessions if this is a planning event
                await self._sync_planning_sessions(
                    calendar_event, time_changed, was_cancelled
                )

        except Exception as e:
            logger.error(f"Error upserting calendar event: {e}")

    async def _sync_scheduler_for_event(
        self, calendar_event: CalendarEvent, time_changed: bool, was_cancelled: bool
    ) -> None:
        """Synchronize scheduler jobs for calendar event changes."""
        try:
            from .scheduler import cancel_haunt, schedule_event_haunt

            # Handle cancellations or completed events
            if was_cancelled or calendar_event.status == EventStatus.CANCELLED:
                if calendar_event.scheduler_job_id:
                    logger.info(
                        f"Cancelling haunt job for cancelled event {calendar_event.event_id}"
                    )
                    cancel_haunt(calendar_event.scheduler_job_id)

                    # Update database
                    async with get_db_session() as db:
                        calendar_event.scheduler_job_id = None

                    # Send agentic Slack notification about cancellation
                    await self._send_agentic_cancellation_notification(calendar_event)
                return

            # Handle upcoming events that need scheduling/rescheduling
            if calendar_event.status == EventStatus.UPCOMING:
                start_time = calendar_event.start_time

                # Only schedule if event is more than 5 minutes in the future
                if start_time > datetime.utcnow() + timedelta(minutes=5):
                    # Calculate reminder time (15 minutes before)
                    reminder_time = start_time - timedelta(minutes=15)

                    if reminder_time > datetime.utcnow():
                        # Cancel existing job if it exists
                        if calendar_event.scheduler_job_id:
                            cancel_haunt(calendar_event.scheduler_job_id)

                        # Schedule new/rescheduled job
                        job_id = schedule_event_haunt(
                            calendar_event.event_id, reminder_time
                        )

                        # Update database with new job ID
                        async with get_db_session() as db:
                            calendar_event.scheduler_job_id = job_id

                        action = "Rescheduled" if time_changed else "Scheduled"
                        logger.info(
                            f"{action} haunt for event {calendar_event.event_id} at {reminder_time}"
                        )

                        # Send agentic notification about move if time changed
                        if time_changed:
                            await self._send_agentic_move_notification(calendar_event)
                    else:
                        logger.info(
                            f"Reminder time {reminder_time} is in the past, not scheduling"
                        )
                else:
                    logger.info(
                        f"Event {calendar_event.event_id} starts too soon, not scheduling reminder"
                    )

        except Exception as e:
            logger.error(
                f"Error syncing scheduler for event {calendar_event.event_id}: {e}"
            )

    async def _sync_planning_sessions(
        self, calendar_event: CalendarEvent, time_changed: bool, was_cancelled: bool
    ) -> None:
        """Update related planning sessions when calendar events change."""
        try:
            async with get_db_session() as db:
                # Find planning sessions linked to this event
                result = await db.execute(
                    select(PlanningSession).where(
                        PlanningSession.event_id == calendar_event.event_id
                    )
                )
                planning_sessions = result.scalars().all()

                for session in planning_sessions:
                    if was_cancelled or calendar_event.status == EventStatus.CANCELLED:
                        # Handle cancelled planning sessions - DON'T mark as complete!
                        # The user still needs to either reschedule or do the planning work
                        session.status = PlanStatus.CANCELLED
                        session.notes = (
                            session.notes or ""
                        ) + f"\n[Auto] Calendar event cancelled at {datetime.utcnow()} - planning still needs to be completed or rescheduled"

                        # Keep haunting! Schedule immediate follow-up to ask user what to do
                        if session.scheduler_job_id:
                            from .scheduler import reschedule_haunt
                            
                            # Reschedule haunt for 5 minutes from now to follow up on cancellation
                            follow_up_time = datetime.utcnow() + timedelta(minutes=5)
                            if reschedule_haunt(session.id, follow_up_time):
                                logger.info(
                                    f"Rescheduled haunt for cancelled session {session.id} to follow up in 5 minutes"
                                )
                            else:
                                logger.warning(
                                    f"Failed to reschedule haunt for cancelled session {session.id}"
                                )

                        logger.info(
                            f"Marked planning session {session.id} as CANCELLED (not complete) - will continue haunting for reschedule/completion"
                        )

                    elif time_changed:
                        # Update session timing for moves
                        session.scheduled_for = calendar_event.start_time
                        session.updated_at = datetime.utcnow()

                        # Reschedule haunt job if exists and session is not complete
                        if (
                            session.scheduler_job_id
                            and session.status != PlanStatus.COMPLETE
                        ):
                            from .scheduler import reschedule_haunt

                            if reschedule_haunt(session.id, calendar_event.start_time):
                                logger.info(
                                    f"Rescheduled planning session {session.id} haunt job"
                                )
                            else:
                                logger.warning(
                                    f"Failed to reschedule planning session {session.id} haunt job"
                                )

        except Exception as e:
            logger.error(
                f"Error syncing planning sessions for event {calendar_event.event_id}: {e}"
            )

    async def _send_agentic_cancellation_notification(
        self, calendar_event: CalendarEvent
    ) -> None:
        """Send agentic Slack notification for event cancellation via AssistantAgent."""
        try:
            # Find planning sessions linked to this event
            async with get_db_session() as db:
                result = db.execute(
                    select(PlanningSession).where(
                        PlanningSession.event_id == calendar_event.event_id
                    )
                )
                planning_sessions = result.scalars().all()

            # Send notification to each linked planning session
            for session in planning_sessions:
                if session.thread_ts and session.channel_id:
                    await self._send_slack_thread_notification(
                        thread_ts=session.thread_ts,
                        channel_id=session.channel_id,
                        notification_type="cancellation",
                        calendar_event=calendar_event,
                        planning_session=session,
                    )

        except Exception as e:
            logger.error(f"Error sending agentic cancellation notification: {e}")

    async def _send_agentic_move_notification(
        self, calendar_event: CalendarEvent
    ) -> None:
        """Send agentic Slack notification for event move via AssistantAgent."""
        try:
            # Find planning sessions linked to this event
            async with get_db_session() as db:
                result = db.execute(
                    select(PlanningSession).where(
                        PlanningSession.event_id == calendar_event.event_id
                    )
                )
                planning_sessions = result.scalars().all()

            # Send notification to each linked planning session
            for session in planning_sessions:
                if session.thread_ts and session.channel_id:
                    await self._send_slack_thread_notification(
                        thread_ts=session.thread_ts,
                        channel_id=session.channel_id,
                        notification_type="move",
                        calendar_event=calendar_event,
                        planning_session=session,
                    )

        except Exception as e:
            logger.error(f"Error sending agentic move notification: {e}")

    async def _send_slack_thread_notification(
        self,
        thread_ts: str,
        channel_id: str,
        notification_type: str,
        calendar_event: CalendarEvent,
        planning_session: PlanningSession,
    ) -> None:
        """Send structured notification to Slack thread using AssistantAgent."""
        try:
            # Import the assistant agent
            from .agents.slack_assistant_agent import get_slack_assistant_agent

            # Create notification context
            notification_context = {
                "type": notification_type,
                "event": {
                    "title": calendar_event.title,
                    "start_time": calendar_event.start_time.strftime('%Y-%m-%d %H:%M'),
                    "end_time": calendar_event.end_time.strftime('%H:%M'),
                    "location": calendar_event.location or "Not specified",
                },
                "session": {
                    "id": planning_session.id,
                    "user_id": planning_session.user_id,
                    "status": planning_session.status.value,
                    "scheduled_for": planning_session.scheduled_for.strftime('%Y-%m-%d %H:%M'),
                },
            }

            # Use the assistant agent to generate structured notification
            agent = await get_slack_assistant_agent()
            
            # Create notification prompt based on type
            if notification_type == "cancellation":
                prompt = f"""Generate a firm but helpful notification that the calendar event "{calendar_event.title}" was cancelled.
                
Event details:
- Was scheduled for: {calendar_event.start_time.strftime('%Y-%m-%d %H:%M')}
- Location: {calendar_event.location or 'Not specified'}

IMPORTANT: The planning work still needs to be completed! The user cannot escape planning just because the calendar event was cancelled.

Ask the user to either:
1. Reschedule the planning session to a new time
2. Complete the planning work right now without a calendar slot

Respond with action "recreate_event" to suggest recreating the calendar event.
Be persistent but supportive - planning is essential and cannot be skipped."""

            else:  # move notification
                prompt = f"""Generate a helpful notification that the calendar event "{calendar_event.title}" was moved.
                
Event details:
- New time: {calendar_event.start_time.strftime('%Y-%m-%d %H:%M')} - {calendar_event.end_time.strftime('%H:%M')}
- Location: {calendar_event.location or 'Not specified'}

The planning reminders have been updated automatically.
Respond with action "status" to acknowledge the change.
Keep the message friendly and informative."""

            # Get the agent response (this will be a PlannerAction)
            response = await agent.process_slack_thread_reply(
                prompt, 
                session_context=notification_context
            )

            # TODO: Actually send to Slack using the bot instance
            # For now, log the structured notification
            logger.info(
                f"AGENTIC NOTIFICATION ({notification_type.upper()}): "
                f"Thread {thread_ts} | Action: {response.action} | "
                f"Event: {calendar_event.title}"
            )

        except Exception as e:
            logger.error(f"Error sending Slack thread notification: {e}")
            # Fallback to simple logging
            fallback_message = (
                f"📅 Event {notification_type.title()}: {calendar_event.title}\n"
                f"⏰ Time: {calendar_event.start_time.strftime('%Y-%m-%d %H:%M')}\n"
                f"📍 Location: {calendar_event.location or 'Not specified'}"
            )
            logger.info(f"FALLBACK NOTIFICATION: {fallback_message}")

    def _parse_datetime(self, time_data: Dict[str, Any]) -> Optional[datetime]:
        """Parse datetime from Google Calendar API format."""
        try:
            if "dateTime" in time_data:
                # Parse RFC3339 datetime (simplified version)
                dt_str = time_data["dateTime"]
                # Remove timezone info for simplicity - just get the base datetime
                if "T" in dt_str:
                    dt_part = (
                        dt_str.split("T")[0]
                        + "T"
                        + dt_str.split("T")[1].split("+")[0].split("-")[0].split("Z")[0]
                    )
                    return datetime.fromisoformat(dt_part)
                else:
                    return datetime.fromisoformat(dt_str)
            elif "date" in time_data:
                # All-day event
                event_date = datetime.strptime(time_data["date"], "%Y-%m-%d").date()
                return datetime.combine(event_date, datetime.min.time())
            else:
                return None
        except Exception as e:
            logger.error(f"Error parsing datetime {time_data}: {e}")
            return None

    async def start_server(
        self, host: str = "0.0.0.0", port: Optional[int] = None
    ) -> None:
        """Start the calendar watch server with scheduler."""
        port = port or 8000  # Default port

        # Start the scheduler
        await start_scheduler()
        logger.info("APScheduler started")

        logger.info(f"Starting Calendar Watch Server on {host}:{port}")

        # Start FastAPI server
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info" if self.config.development else "warning",
        )
        server = uvicorn.Server(config)
        await server.serve()


async def main() -> None:
    """Main entry point for the calendar watch server."""
    try:
        config = get_config()
        server = CalendarWatchServer(config)
        await server.start_server()
    except KeyboardInterrupt:
        logger.info("Calendar Watch Server stopped by user")
    except Exception as e:
        logger.error(f"Error starting Calendar Watch Server: {e}")
        raise


def sync_main() -> None:
    """Synchronous main entry point."""
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    sync_main()

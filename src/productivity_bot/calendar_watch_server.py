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
from .models import CalendarEvent, CalendarSync, EventStatus
from .scheduler import get_scheduler, schedule_event_haunt, start_scheduler

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
                result = await db.execute(
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

                await db.commit()
                logger.info(f"Calendar sync completed for {calendar_id}")

        except Exception as e:
            logger.error(f"Error syncing calendar events: {e}")

            # Update sync record with error
            if sync_record:
                async with get_db_session() as db:
                    sync_record.sync_error = str(e)
                    sync_record.last_sync_at = datetime.utcnow()
                    await db.commit()

    async def _upsert_calendar_event(self, event_data: Dict[str, Any]):
        """Upsert a calendar event and schedule haunting if needed."""
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
                result = await db.execute(
                    select(CalendarEvent).where(CalendarEvent.event_id == event_id)
                )
                existing_event = result.scalar_one_or_none()

                if existing_event:
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

                await db.commit()

                # Schedule haunt job if event is upcoming
                if (
                    event_status == EventStatus.UPCOMING
                    and start_time > datetime.utcnow() + timedelta(minutes=5)
                ):

                    # Schedule reminder 15 minutes before event
                    reminder_time = start_time - timedelta(minutes=15)

                    if reminder_time > datetime.utcnow():
                        job_id = schedule_event_haunt(event_id, reminder_time)
                        calendar_event.scheduler_job_id = job_id
                        await db.commit()

                        logger.info(
                            f"Scheduled haunt for event {event_id} at {reminder_time}"
                        )

        except Exception as e:
            logger.error(f"Error upserting calendar event: {e}")

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

    async def start_server(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """Start the calendar watch server with scheduler."""
        port = port or self.config.port

        # Start the scheduler
        await start_scheduler()
        logger.info("APScheduler started")

        logger.info(f"Starting Calendar Watch Server on {host}:{port}")

        # Start FastAPI server
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info" if self.config.debug else "warning",
        )
        server = uvicorn.Server(config)
        await server.serve()


async def main():
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


def sync_main():
    """Synchronous main entry point."""
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    sync_main()

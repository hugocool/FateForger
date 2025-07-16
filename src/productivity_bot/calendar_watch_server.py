"""
Calendar Watch Server - FastAPI server for calendar webhooks and monitoring.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import uvicorn

from .common import Config, logger, format_slack_message


class CalendarWatchServer:
    """
    FastAPI server that watches calendar events and integrates with Slack.
    Receives webhooks from calendar services and triggers appropriate actions.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        if not self.config.validate():
            raise ValueError("Invalid configuration")

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
                background_tasks.add_task(self._process_calendar_event, data)

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
                    f"Google Calendar notification - Channel: {channel_id}, State: {resource_state}"
                )

                if resource_state == "sync":
                    return {"status": "sync_acknowledged"}

                # Process the calendar change
                webhook_data = {
                    "source": "google_calendar",
                    "channel_id": channel_id,
                    "resource_id": resource_id,
                    "state": resource_state,
                    "timestamp": datetime.now().isoformat(),
                }

                background_tasks.add_task(self._process_calendar_event, webhook_data)

                return {"status": "processed"}

            except Exception as e:
                logger.error(f"Error processing Google Calendar webhook: {e}")
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/api/events/upcoming")
        async def get_upcoming_events():
            """Get upcoming calendar events."""
            # TODO: Integrate with calendar APIs
            return {"events": [], "message": "Calendar integration not yet implemented"}

        @self.app.post("/api/reminders")
        async def create_reminder(request: Request):
            """Create a new reminder."""
            try:
                data = await request.json()
                # TODO: Integrate with HaunterBot
                return {"status": "reminder_created", "data": data}
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

    async def _process_calendar_event(self, event_data: Dict[str, Any]):
        """Process calendar event in background."""
        try:
            logger.info(f"Processing calendar event: {event_data}")

            # Determine event type and take appropriate action
            source = event_data.get("source", "unknown")

            if source == "google_calendar":
                await self._handle_google_calendar_event(event_data)
            else:
                await self._handle_generic_calendar_event(event_data)

        except Exception as e:
            logger.error(f"Error processing calendar event: {e}")

    async def _handle_google_calendar_event(self, event_data: Dict[str, Any]):
        """Handle Google Calendar specific events."""
        state = event_data.get("state")

        if state == "exists":
            # Calendar was updated
            logger.info("Calendar updated - checking for new events")
            # TODO: Fetch recent changes and notify relevant channels

    async def _handle_generic_calendar_event(self, event_data: Dict[str, Any]):
        """Handle generic calendar events."""
        # TODO: Parse event data and trigger appropriate actions
        logger.info(f"Handling generic calendar event: {event_data}")

    def start(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """Start the calendar watch server."""
        port = port or self.config.port
        logger.info(f"Starting Calendar Watch Server on {host}:{port}")

        uvicorn.run(
            self.app,
            host=host,
            port=port,
            log_level="info" if self.config.debug else "warning",
        )


def main():
    """Main entry point for the calendar watch server."""
    try:
        config = Config()
        server = CalendarWatchServer(config)
        server.start()
    except KeyboardInterrupt:
        logger.info("Calendar Watch Server stopped by user")
    except Exception as e:
        logger.error(f"Error starting Calendar Watch Server: {e}")
        raise


if __name__ == "__main__":
    main()

"""
Main orchestrator for the Admonish productivity bot.
Coordinates Slack Bot, APScheduler, FastAPI server, and MCP communication.
"""

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from .common import (
    cleanup_services,
    get_config,
    health_check_database,
    health_check_mcp,
    health_check_slack,
    initialize_services,
    setup_logging,
)


class AdmonishOrchestrator:
    """Main orchestrator for all Admonish services."""

    def __init__(self):
        self.config = get_config()
        self.logger = setup_logging()
        self.running = False

    async def initialize(self):
        """Initialize all services."""
        self.logger.info("Initializing Admonish Orchestrator...")

        # Initialize all common services
        await initialize_services()

        self.logger.info("All services initialized successfully")

    async def health_check(self) -> dict:
        """Perform comprehensive health check."""
        checks = {
            "mcp": await health_check_mcp(),
            "slack": await health_check_slack(),
            "database": await health_check_database(),
        }

        all_healthy = all(checks.values())
        self.logger.info(
            f"Health check results: {checks} (Overall: {'Healthy' if all_healthy else 'Unhealthy'})"
        )

        return {"status": "healthy" if all_healthy else "unhealthy", "services": checks}

    async def start(self):
        """Start all services."""
        if self.running:
            return

        self.logger.info("Starting Admonish services...")
        self.running = True

        # Perform initial health check
        health_status = await self.health_check()
        if health_status["status"] != "healthy":
            self.logger.warning("Starting with some services unhealthy")

        # Import and start individual services here
        # (This would be where you import and start your specific bots)

        self.logger.info("All services started successfully")

        # Keep running
        try:
            while self.running:
                await asyncio.sleep(60)  # Health check every minute
                await self.health_check()
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
            await self.stop()

    async def stop(self):
        """Stop all services gracefully."""
        if not self.running:
            return

        self.logger.info("Stopping Admonish services...")
        self.running = False

        # Cleanup all services
        await cleanup_services()

        self.logger.info("All services stopped")

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(self.stop())

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


async def main():
    """Main entry point."""
    orchestrator = AdmonishOrchestrator()
    orchestrator.setup_signal_handlers()

    try:
        await orchestrator.initialize()
        await orchestrator.start()
    except KeyboardInterrupt:
        orchestrator.logger.info("Received keyboard interrupt")
    except Exception as e:
        orchestrator.logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())

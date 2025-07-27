"""
Shared Session Manager for Haunter-Planner coupling.

This module provides centralized session management shared between the planner and haunter agents.
It ensures both agents work with the same session context, thread mappings, and cleanup logic.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import and_, select, update

from .common import get_logger
from .database import get_db_session
from .models import PlanningSession, PlanStatus

logger = get_logger("session_manager")


class SessionRegistry:
    """
    Centralized registry for managing planning sessions shared between agents.

    Provides:
    - Unified session creation with shared session_id
    - Thread mapping (session_id -> thread_ts, channel_id)
    - Session cleanup for orphaned sessions (>32 hours)
    - Emoji feedback handling for mark_done
    """

    def __init__(self):
        """Initialize the session registry."""
        # In-memory cache for fast lookups (session_id -> session_data)
        self._session_cache: Dict[str, Dict] = {}

        # Thread mapping (thread_ts -> session_id)
        self._thread_to_session: Dict[str, str] = {}

    async def create_planning_session(
        self,
        user_id: str,
        event_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        channel_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> Tuple[str, PlanningSession]:
        """
        Create a new planning session with shared context.

        Args:
            user_id: Slack user ID
            event_id: Associated calendar event ID
            thread_ts: Slack thread timestamp (if starting from thread)
            channel_id: Slack channel ID
            scheduled_for: When the session is scheduled for

        Returns:
            Tuple of (session_id, PlanningSession)
        """
        session_id = str(uuid4())

        try:
            async with get_db_session() as db:
                # Create the planning session in database
                planning_session = PlanningSession(
                    user_id=user_id,
                    date=datetime.now().date(),
                    event_id=event_id,
                    status=PlanStatus.NOT_STARTED,
                    scheduled_for=scheduled_for or datetime.now(),
                    thread_ts=thread_ts,
                    channel_id=channel_id,
                )

                db.add(planning_session)
                await db.commit()
                await db.refresh(planning_session)

                # Cache the session data for fast lookups
                session_data = {
                    "session_id": session_id,
                    "db_id": planning_session.id,
                    "user_id": user_id,
                    "event_id": event_id,
                    "thread_ts": thread_ts,
                    "channel_id": channel_id,
                    "status": planning_session.status,
                    "created_at": planning_session.created_at,
                    "scheduled_for": planning_session.scheduled_for,
                }

                self._session_cache[session_id] = session_data

                # Map thread to session if we have thread info
                if thread_ts:
                    self._thread_to_session[thread_ts] = session_id

                logger.info(
                    f"Created planning session {session_id} for user {user_id} "
                    f"(DB ID: {planning_session.id})"
                )

                return session_id, planning_session

        except Exception as e:
            logger.error(f"Failed to create planning session: {e}")
            raise

    async def get_session_by_thread(self, thread_ts: str) -> Optional[Dict]:
        """
        Get session data by Slack thread timestamp.

        Args:
            thread_ts: Slack thread timestamp

        Returns:
            Session data dict or None if not found
        """
        # Check cache first
        session_id = self._thread_to_session.get(thread_ts)
        if session_id and session_id in self._session_cache:
            return self._session_cache[session_id]

        # Fallback to database lookup
        try:
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession).where(
                        PlanningSession.thread_ts == thread_ts
                    )
                )
                session = result.scalar_one_or_none()

                if session:
                    # Generate session_id for existing session if not cached
                    session_id = str(uuid4())
                    session_data = {
                        "session_id": session_id,
                        "db_id": session.id,
                        "user_id": session.user_id,
                        "event_id": session.event_id,
                        "thread_ts": session.thread_ts,
                        "channel_id": session.channel_id,
                        "status": session.status,
                        "created_at": session.created_at,
                        "scheduled_for": session.scheduled_for,
                    }

                    # Cache for future lookups
                    self._session_cache[session_id] = session_data
                    self._thread_to_session[thread_ts] = session_id

                    return session_data

        except Exception as e:
            logger.error(f"Error looking up session by thread {thread_ts}: {e}")

        return None

    async def get_session_by_id(self, session_id: str) -> Optional[Dict]:
        """
        Get session data by session ID.

        Args:
            session_id: UUID session identifier

        Returns:
            Session data dict or None if not found
        """
        # Check cache first
        if session_id in self._session_cache:
            return self._session_cache[session_id]

        # If not in cache, it might have been evicted - this is expected
        logger.debug(f"Session {session_id} not in cache (may have been evicted)")
        return None

    async def update_session_thread(
        self, session_id: str, thread_ts: str, channel_id: str
    ) -> bool:
        """
        Update session with thread information.

        Args:
            session_id: Session UUID
            thread_ts: Slack thread timestamp
            channel_id: Slack channel ID

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            session_data = self._session_cache.get(session_id)
            if not session_data:
                logger.warning(f"Session {session_id} not found in cache")
                return False

            # Update database
            async with get_db_session() as db:
                await db.execute(
                    update(PlanningSession)
                    .where(PlanningSession.id == session_data["db_id"])
                    .values(thread_ts=thread_ts, channel_id=channel_id)
                )
                await db.commit()

            # Update cache
            session_data["thread_ts"] = thread_ts
            session_data["channel_id"] = channel_id
            self._thread_to_session[thread_ts] = session_id

            logger.info(f"Updated session {session_id} with thread {thread_ts}")
            return True

        except Exception as e:
            logger.error(f"Failed to update session thread: {e}")
            return False

    async def mark_session_done(self, session_id: str, add_emoji: bool = True) -> bool:
        """
        Mark session as complete and optionally add ✅ emoji to thread.

        Args:
            session_id: Session UUID
            add_emoji: Whether to add checkmark emoji to original thread message

        Returns:
            True if marked done successfully, False otherwise
        """
        try:
            session_data = self._session_cache.get(session_id)
            if not session_data:
                logger.warning(f"Session {session_id} not found in cache")
                return False

            # Update database
            async with get_db_session() as db:
                await db.execute(
                    update(PlanningSession)
                    .where(PlanningSession.id == session_data["db_id"])
                    .values(status=PlanStatus.COMPLETE, completed_at=datetime.now())
                )
                await db.commit()

            # Update cache
            session_data["status"] = PlanStatus.COMPLETE
            session_data["completed_at"] = datetime.now()

            # Add emoji to thread if requested and we have thread info
            if add_emoji and session_data.get("thread_ts"):
                await self._add_completion_emoji(
                    session_data["channel_id"], session_data["thread_ts"]
                )

            logger.info(f"Marked session {session_id} as complete")
            return True

        except Exception as e:
            logger.error(f"Failed to mark session done: {e}")
            return False

    async def _add_completion_emoji(self, channel_id: str, thread_ts: str) -> None:
        """
        Add ✅ emoji to the original thread message.

        Args:
            channel_id: Slack channel ID
            thread_ts: Thread timestamp of the original message
        """
        try:
            from slack_sdk.web.async_client import AsyncWebClient

            from .common import get_config

            config = get_config()
            client = AsyncWebClient(token=config.slack_bot_token)

            # Add reaction to the thread parent message
            await client.reactions_add(
                channel=channel_id, timestamp=thread_ts, name="white_check_mark"
            )

            logger.info(f"Added ✅ emoji to thread {thread_ts}")

        except Exception as e:
            logger.warning(f"Failed to add completion emoji: {e}")

    async def cleanup_orphaned_sessions(self, max_age_hours: int = 32) -> int:
        """
        Clean up sessions older than specified hours.

        Args:
            max_age_hours: Maximum age in hours before session is considered orphaned

        Returns:
            Number of sessions cleaned up
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleaned_count = 0

        try:
            async with get_db_session() as db:
                # Find orphaned sessions
                result = await db.execute(
                    select(PlanningSession).where(
                        and_(
                            PlanningSession.created_at < cutoff_time,
                            PlanningSession.status.in_(
                                [PlanStatus.NOT_STARTED, PlanStatus.IN_PROGRESS]
                            ),
                        )
                    )
                )
                orphaned_sessions = result.scalars().all()

                # Mark them as archived and send summary (TODO: implement summary)
                for session in orphaned_sessions:
                    await db.execute(
                        update(PlanningSession)
                        .where(PlanningSession.id == session.id)
                        .values(status=PlanStatus.COMPLETE)  # Archive as complete
                    )

                    # Remove from cache if present
                    session_id_to_remove = None
                    for sid, data in self._session_cache.items():
                        if data.get("db_id") == session.id:
                            session_id_to_remove = sid
                            break

                    if session_id_to_remove:
                        del self._session_cache[session_id_to_remove]
                        # Also clean up thread mapping
                        if session.thread_ts in self._thread_to_session:
                            del self._thread_to_session[session.thread_ts]

                    cleaned_count += 1

                    # TODO: Send closure summary to user
                    logger.info(
                        f"Archived orphaned session {session.id} for user {session.user_id}"
                    )

                await db.commit()

                if cleaned_count > 0:
                    logger.info(
                        f"Cleaned up {cleaned_count} orphaned sessions older than {max_age_hours}h"
                    )

        except Exception as e:
            logger.error(f"Error during orphaned session cleanup: {e}")

        return cleaned_count

    async def get_active_sessions_for_user(self, user_id: str) -> List[Dict]:
        """
        Get all active sessions for a user.

        Args:
            user_id: Slack user ID

        Returns:
            List of active session data dicts
        """
        active_sessions = []

        try:
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession)
                    .where(
                        and_(
                            PlanningSession.user_id == user_id,
                            PlanningSession.status.in_(
                                [PlanStatus.NOT_STARTED, PlanStatus.IN_PROGRESS]
                            ),
                        )
                    )
                    .order_by(PlanningSession.scheduled_for.desc())
                )
                sessions = result.scalars().all()

                for session in sessions:
                    session_data = {
                        "db_id": session.id,
                        "user_id": session.user_id,
                        "event_id": session.event_id,
                        "thread_ts": session.thread_ts,
                        "channel_id": session.channel_id,
                        "status": session.status,
                        "created_at": session.created_at,
                        "scheduled_for": session.scheduled_for,
                    }
                    active_sessions.append(session_data)

        except Exception as e:
            logger.error(f"Error getting active sessions for user {user_id}: {e}")

        return active_sessions


# Global session registry instance
_session_registry: Optional[SessionRegistry] = None


def get_session_registry() -> SessionRegistry:
    """Get the global session registry instance."""
    global _session_registry
    if _session_registry is None:
        _session_registry = SessionRegistry()
    return _session_registry

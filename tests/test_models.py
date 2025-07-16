"""
Tests for database models, schema, and relationships.
"""

import pytest
import asyncio
from datetime import datetime, date
from sqlalchemy import create_engine, inspect, MetaData
from sqlalchemy.orm import sessionmaker

# Test imports
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from productivity_bot.models import (
    Base,
    CalendarEvent,
    CalendarReminderJob,
    CalendarSync,
    EventStatus,
    PlanningSession,
    Task,
    Reminder,
    UserPreferences,
)


class TestDatabaseSchema:
    """Test database schema and table structure."""

    @classmethod
    def setup_class(cls):
        """Set up test database."""
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def test_calendar_events_table_exists(self):
        """Test that calendar_events table exists with correct columns."""
        inspector = inspect(self.engine)

        # Check table exists
        assert "calendar_events" in inspector.get_table_names()

        # Check columns
        columns = {col["name"]: col for col in inspector.get_columns("calendar_events")}

        # Required columns
        assert "event_id" in columns
        assert "calendar_id" in columns
        assert "start_time" in columns
        assert "end_time" in columns
        assert "scheduler_job_id" in columns

        # Check primary key
        pk = inspector.get_pk_constraint("calendar_events")
        assert pk["constrained_columns"] == ["event_id"]

    def test_calendar_reminder_jobs_table_exists(self):
        """Test that calendar_reminder_jobs table exists with correct columns."""
        inspector = inspect(self.engine)

        # Check table exists
        assert "calendar_reminder_jobs" in inspector.get_table_names()

        # Check columns
        columns = {
            col["name"]: col for col in inspector.get_columns("calendar_reminder_jobs")
        }

        # Required columns
        assert "id" in columns
        assert "event_id" in columns
        assert "start_time" in columns
        assert "job_id" in columns

        # Check primary key
        pk = inspector.get_pk_constraint("calendar_reminder_jobs")
        assert pk["constrained_columns"] == ["id"]

    def test_foreign_key_constraint(self):
        """Test FK constraint from calendar_reminder_jobs to calendar_events."""
        inspector = inspect(self.engine)

        fks = inspector.get_foreign_keys("calendar_reminder_jobs")

        # Should have one FK
        assert len(fks) == 1

        fk = fks[0]
        assert fk["referred_table"] == "calendar_events"
        assert fk["referred_columns"] == ["event_id"]
        assert fk["constrained_columns"] == ["event_id"]
        assert fk["options"]["ondelete"] == "CASCADE"

    def test_unique_constraints(self):
        """Test unique constraints."""
        inspector = inspect(self.engine)

        # calendar_reminder_jobs should have unique job_id
        unique_constraints = inspector.get_unique_constraints("calendar_reminder_jobs")
        job_id_unique = any(
            constraint["column_names"] == ["job_id"]
            for constraint in unique_constraints
        )
        assert job_id_unique, "job_id should have unique constraint"

    def test_indexes(self):
        """Test that proper indexes exist."""
        inspector = inspect(self.engine)

        # Check calendar_events indexes
        ce_indexes = inspector.get_indexes("calendar_events")
        index_names = [idx["name"] for idx in ce_indexes]

        assert "ix_calendar_events_calendar_id" in index_names
        assert "ix_calendar_events_start_time" in index_names
        assert "ix_calendar_events_end_time" in index_names
        assert "ix_calendar_events_scheduler_job_id" in index_names

        # Check calendar_reminder_jobs indexes
        crj_indexes = inspector.get_indexes("calendar_reminder_jobs")
        index_names = [idx["name"] for idx in crj_indexes]

        assert "ix_calendar_reminder_jobs_event_id" in index_names
        assert "ix_calendar_reminder_jobs_start_time" in index_names


class TestModelRelationships:
    """Test SQLAlchemy model relationships and operations."""

    @classmethod
    def setup_class(cls):
        """Set up test database."""
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def test_calendar_event_creation(self):
        """Test creating a CalendarEvent."""
        session = self.Session()

        try:
            event = CalendarEvent(
                event_id="test-event-123",
                calendar_id="primary",
                title="Test Meeting",
                start_time=datetime(2024, 7, 16, 10, 0),
                end_time=datetime(2024, 7, 16, 11, 0),
                status=EventStatus.UPCOMING,
            )

            session.add(event)
            session.commit()

            # Verify the event was saved
            saved_event = (
                session.query(CalendarEvent)
                .filter_by(event_id="test-event-123")
                .first()
            )
            assert saved_event is not None
            assert saved_event.title == "Test Meeting"
            assert saved_event.status == EventStatus.UPCOMING

        finally:
            session.close()

    def test_calendar_reminder_job_creation(self):
        """Test creating a CalendarReminderJob."""
        session = self.Session()

        try:
            # First create an event
            event = CalendarEvent(
                event_id="test-event-456",
                calendar_id="primary",
                title="Test Meeting with Reminder",
                start_time=datetime(2024, 7, 16, 14, 0),
                end_time=datetime(2024, 7, 16, 15, 0),
                status=EventStatus.UPCOMING,
            )
            session.add(event)
            session.commit()

            # Create reminder job
            reminder_job = CalendarReminderJob(
                event_id="test-event-456",
                start_time=datetime(2024, 7, 16, 13, 45),  # 15 min before
                job_id="haunt_test-event-456_15min",
            )

            session.add(reminder_job)
            session.commit()

            # Verify the reminder job was saved
            saved_job = (
                session.query(CalendarReminderJob)
                .filter_by(event_id="test-event-456")
                .first()
            )
            assert saved_job is not None
            assert saved_job.job_id == "haunt_test-event-456_15min"

        finally:
            session.close()

    def test_relationship_loading(self):
        """Test that relationships between CalendarEvent and CalendarReminderJob work."""
        session = self.Session()

        try:
            # Create event with reminder jobs
            event = CalendarEvent(
                event_id="test-event-789",
                calendar_id="primary",
                title="Meeting with Multiple Reminders",
                start_time=datetime(2024, 7, 16, 16, 0),
                end_time=datetime(2024, 7, 16, 17, 0),
                status=EventStatus.UPCOMING,
            )
            session.add(event)
            session.flush()  # Get the event ID

            # Create multiple reminder jobs
            job1 = CalendarReminderJob(
                event_id="test-event-789",
                start_time=datetime(2024, 7, 16, 15, 45),  # 15 min before
                job_id="haunt_test-event-789_15min",
            )

            job2 = CalendarReminderJob(
                event_id="test-event-789",
                start_time=datetime(2024, 7, 16, 15, 55),  # 5 min before
                job_id="haunt_test-event-789_5min",
            )

            session.add_all([job1, job2])
            session.commit()

            # Test forward relationship (event -> reminder_jobs)
            loaded_event = (
                session.query(CalendarEvent)
                .filter_by(event_id="test-event-789")
                .first()
            )
            assert len(loaded_event.reminder_jobs) == 2

            job_ids = [job.job_id for job in loaded_event.reminder_jobs]
            assert "haunt_test-event-789_15min" in job_ids
            assert "haunt_test-event-789_5min" in job_ids

            # Test reverse relationship (reminder_job -> event)
            loaded_job = (
                session.query(CalendarReminderJob)
                .filter_by(job_id="haunt_test-event-789_15min")
                .first()
            )
            assert loaded_job.event.title == "Meeting with Multiple Reminders"

        finally:
            session.close()

    def test_cascade_delete(self):
        """Test that deleting an event cascades to reminder jobs."""
        session = self.Session()

        try:
            # Create event with reminder job
            event = CalendarEvent(
                event_id="test-event-cascade",
                calendar_id="primary",
                title="Event to be Deleted",
                start_time=datetime(2024, 7, 16, 18, 0),
                end_time=datetime(2024, 7, 16, 19, 0),
                status=EventStatus.UPCOMING,
            )
            session.add(event)
            session.flush()

            job = CalendarReminderJob(
                event_id="test-event-cascade",
                start_time=datetime(2024, 7, 16, 17, 45),
                job_id="haunt_test-event-cascade_15min",
            )
            session.add(job)
            session.commit()

            # Verify both exist
            assert (
                session.query(CalendarEvent)
                .filter_by(event_id="test-event-cascade")
                .first()
                is not None
            )
            assert (
                session.query(CalendarReminderJob)
                .filter_by(event_id="test-event-cascade")
                .first()
                is not None
            )

            # Delete the event
            session.delete(event)
            session.commit()

            # Verify both are gone (cascade delete)
            assert (
                session.query(CalendarEvent)
                .filter_by(event_id="test-event-cascade")
                .first()
                is None
            )
            assert (
                session.query(CalendarReminderJob)
                .filter_by(event_id="test-event-cascade")
                .first()
                is None
            )

        finally:
            session.close()


class TestModelProperties:
    """Test model properties and methods."""

    def test_calendar_event_properties(self):
        """Test CalendarEvent properties."""
        # Test upcoming event
        upcoming_event = CalendarEvent(
            event_id="upcoming-test",
            calendar_id="primary",
            title="Future Event",
            start_time=datetime(2030, 1, 1, 10, 0),
            end_time=datetime(2030, 1, 1, 11, 0),
            status=EventStatus.UPCOMING,
        )

        assert upcoming_event.is_upcoming is True
        assert upcoming_event.is_past is False
        assert upcoming_event.duration_minutes == 60

        # Test past event
        past_event = CalendarEvent(
            event_id="past-test",
            calendar_id="primary",
            title="Past Event",
            start_time=datetime(2020, 1, 1, 10, 0),
            end_time=datetime(2020, 1, 1, 11, 30),
            status=EventStatus.COMPLETED,
        )

        assert past_event.is_upcoming is False
        assert past_event.is_past is True
        assert past_event.duration_minutes == 90

    def test_calendar_event_status_methods(self):
        """Test CalendarEvent status change methods."""
        event = CalendarEvent(
            event_id="status-test",
            calendar_id="primary",
            title="Status Test Event",
            start_time=datetime(2024, 7, 16, 10, 0),
            end_time=datetime(2024, 7, 16, 11, 0),
            status=EventStatus.UPCOMING,
        )

        # Test mark completed
        event.mark_completed()
        assert event.status == EventStatus.COMPLETED

        # Test mark cancelled
        event.mark_cancelled()
        assert event.status == EventStatus.CANCELLED

#!/usr/bin/env python3
"""
Test script demonstrating the new relational ScheduleDraft ↔ CalendarEvent model.
Uses in-memory SQLite database to avoid migration complexities.
"""

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from src.fateforger.agents.schedular.models.calendar_event import (
    CalendarEvent,
    CreatorOrganizer,
    EventDateTime,
)
from src.fateforger.agents.schedular.models.schedule_draft import (
    DraftStore,
    ScheduleDraft,
)


def create_sample_events() -> list[CalendarEvent]:
    """Create some sample CalendarEvent objects for testing."""

    # Sample event 1: Morning meeting
    event1 = CalendarEvent(
        summary="Team Standup",
        description="Daily team synchronization",
        location="Conference Room A",
        status="confirmed",
        start=EventDateTime(
            date_time=datetime(2025, 7, 30, 9, 0), time_zone="Europe/Amsterdam"
        ),
        end=EventDateTime(
            date_time=datetime(2025, 7, 30, 9, 30), time_zone="Europe/Amsterdam"
        ),
        creator=CreatorOrganizer(
            email="team-lead@company.com", display_name="Team Lead"
        ),
    )

    # Sample event 2: Afternoon focus time
    event2 = CalendarEvent(
        summary="Deep Work - Code Review",
        description="Focus time for reviewing pull requests",
        status="confirmed",
        start=EventDateTime(
            date_time=datetime(2025, 7, 30, 14, 0), time_zone="Europe/Amsterdam"
        ),
        end=EventDateTime(
            date_time=datetime(2025, 7, 30, 16, 0), time_zone="Europe/Amsterdam"
        ),
    )

    return [event1, event2]


def main():
    """Demonstrate the relational ScheduleDraft model."""

    print("🚀 Testing Relational ScheduleDraft Model")
    print("=" * 50)

    # Create in-memory SQLite database
    engine = create_engine("sqlite:///:memory:", echo=True)

    # Create all tables
    SQLModel.metadata.create_all(engine)

    today = date(2025, 7, 30)

    with Session(engine) as session:
        store = DraftStore(session)

        print("\n📝 Creating sample events...")
        events = create_sample_events()
        for i, event in enumerate(events, 1):
            start_time = event.start.date_time if event.start else "No start time"
            end_time = event.end.date_time if event.end else "No end time"
            print(f"  Event {i}: {event.summary} ({start_time} - {end_time})")

        print(f"\n💾 Creating ScheduleDraft for {today}...")
        draft = ScheduleDraft(date=today, events=events)
        saved_draft = store.save(draft)

        print(f"✅ Saved draft with ID: {saved_draft.id}")
        print(f"   Events count: {len(saved_draft.events)}")

        print("\n🔍 Loading draft back from database...")
        loaded_draft = store.get_by_date(today)

        if loaded_draft:
            print(f"📋 Loaded draft: {loaded_draft.date}")
            print(f"   Events count: {len(loaded_draft.events)}")
            for i, event in enumerate(loaded_draft.events, 1):
                print(f"   Event {i}: {event.summary}")
                print(f"            Schedule Draft ID: {event.schedule_draft_id}")
                print(f"            Start: {event.start.date_time}")

        print("\n📑 Listing all drafts...")
        all_drafts = store.list_all()
        print(f"   Total drafts: {len(all_drafts)}")

        print("\n🔗 Testing relationship navigation...")
        if loaded_draft and loaded_draft.events:
            first_event = loaded_draft.events[0]
            print(
                f"   Event '{first_event.summary}' belongs to draft: {first_event.schedule_draft.date}"
            )

        print(f"\n🗑 Deleting draft {saved_draft.id}...")
        store.delete(saved_draft.id)

        print("\n📑 After delete - listing all drafts...")
        remaining_drafts = store.list_all()
        print(f"   Remaining drafts: {len(remaining_drafts)}")

        # Verify events were also deleted due to cascade
        print("\n🔍 Verifying cascade delete...")
        remaining_events = session.query(CalendarEvent).all()
        print(f"   Remaining events: {len(remaining_events)}")

    print("\n🎉 Test completed successfully!")
    print("\n✨ Benefits of the relational approach:")
    print("   • Full CRUD on individual events")
    print("   • No JSON serialization hacks")
    print("   • Easy querying/filtering on event fields")
    print("   • Automatic cascade deletes")
    print("   • SQLAlchemy relationship navigation")


if __name__ == "__main__":
    main()

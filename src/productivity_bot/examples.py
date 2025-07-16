"""
Example usage of the PlanningSession model and database services.
"""

import asyncio
from datetime import datetime, date, timedelta
from productivity_bot.models import PlanStatus, TaskPriority, TaskStatus
from productivity_bot.database import (
    PlanningSessionService,
    TaskService,
    ReminderService,
    UserPreferencesService,
)


async def example_usage():
    """Example of how to use the planning session system."""

    user_id = "U123456789"  # Slack user ID
    today = date.today()
    tomorrow = today + timedelta(days=1)

    print("🚀 Creating a planning session...")

    # Create a planning session for tomorrow
    session = await PlanningSessionService.create_session(
        user_id=user_id,
        session_date=tomorrow,
        scheduled_for=datetime.combine(tomorrow, datetime.min.time().replace(hour=9)),
        goals="Complete project tasks and prepare for meetings",
    )

    print(f"✅ Created session: {session}")

    # Add some tasks to the session
    print("\n📋 Adding tasks...")

    task1 = await TaskService.create_task(
        planning_session_id=session.id,
        title="Review project requirements",
        description="Go through the requirements document and identify any gaps",
        priority=TaskPriority.HIGH,
        estimated_duration_minutes=60,
        scheduled_start=datetime.combine(tomorrow, datetime.min.time().replace(hour=9)),
        scheduled_end=datetime.combine(tomorrow, datetime.min.time().replace(hour=10)),
    )

    task2 = await TaskService.create_task(
        planning_session_id=session.id,
        title="Write unit tests",
        description="Add test coverage for the new features",
        priority=TaskPriority.MEDIUM,
        estimated_duration_minutes=120,
        scheduled_start=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=10, minute=30)
        ),
        scheduled_end=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=12, minute=30)
        ),
    )

    task3 = await TaskService.create_task(
        planning_session_id=session.id,
        title="Team standup meeting",
        description="Daily standup with the development team",
        priority=TaskPriority.URGENT,
        estimated_duration_minutes=30,
        scheduled_start=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=14)
        ),
        scheduled_end=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=14, minute=30)
        ),
    )

    print(f"✅ Created task 1: {task1.title}")
    print(f"✅ Created task 2: {task2.title}")
    print(f"✅ Created task 3: {task3.title}")

    # Update session status to in progress
    print("\n🎯 Starting the planning session...")
    await PlanningSessionService.update_session_status(
        session.id, PlanStatus.IN_PROGRESS
    )

    # Get the updated session with tasks
    updated_session = await PlanningSessionService.get_session_by_id(session.id)
    print(f"✅ Session status: {updated_session.status.value}")
    print(f"📋 Session has {len(updated_session.tasks)} tasks")

    # Create some reminders
    print("\n⏰ Setting up reminders...")

    from productivity_bot.models import ReminderType

    # Reminder for the planning session
    reminder1 = await ReminderService.create_reminder(
        user_id=user_id,
        reminder_type=ReminderType.PLANNING_SESSION,
        message="Time to start your planning session!",
        scheduled_at=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=8, minute=45)
        ),
    )

    # Reminder for the urgent task
    reminder2 = await ReminderService.create_reminder(
        user_id=user_id,
        task_id=task3.id,
        reminder_type=ReminderType.TASK_START,
        message=f"Reminder: {task3.title} starts in 15 minutes",
        scheduled_at=datetime.combine(
            tomorrow, datetime.min.time().replace(hour=13, minute=45)
        ),
    )

    print(f"✅ Created reminder for planning session")
    print(f"✅ Created reminder for urgent task")

    # Get user preferences
    print("\n⚙️ Managing user preferences...")

    preferences = await UserPreferencesService.get_or_create_preferences(user_id)
    print(f"✅ User timezone: {preferences.timezone}")
    print(
        f"📋 Default task duration: {preferences.default_task_duration_minutes} minutes"
    )

    # Update preferences
    await UserPreferencesService.update_preferences(
        user_id=user_id,
        preferred_planning_time="09:00",
        default_reminder_minutes_before=10,
        auto_schedule_tasks=True,
    )
    print("✅ Updated user preferences")

    # Simulate task completion
    print("\n✅ Completing a task...")
    await TaskService.update_task_status(task1.id, TaskStatus.COMPLETED)

    completed_task = await TaskService.get_task_by_id(task1.id)
    print(
        f"✅ Task '{completed_task.title}' completed at {completed_task.completed_at}"
    )

    # Get upcoming tasks
    print("\n📅 Getting upcoming tasks...")
    upcoming_tasks = await TaskService.get_user_tasks_due_soon(user_id, hours_ahead=48)
    print(f"📋 Found {len(upcoming_tasks)} upcoming tasks")

    for task in upcoming_tasks:
        print(
            f"  - {task.title} (due: {task.scheduled_end}, priority: {task.priority.value})"
        )

    # Complete the planning session
    print("\n🎉 Completing the planning session...")
    await PlanningSessionService.update_session_status(session.id, PlanStatus.COMPLETE)

    final_session = await PlanningSessionService.get_session_by_id(session.id)
    print(f"✅ Session completed at: {final_session.completed_at}")

    print("\n🎯 Planning session example completed successfully!")


if __name__ == "__main__":
    asyncio.run(example_usage())

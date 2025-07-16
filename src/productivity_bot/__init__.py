"""
Productivity Bot - Does the productivity for you.

A Slack bot that helps with planning, reminders, and calendar management.
"""

__version__ = "0.1.0"
__author__ = "hugocool"
__email__ = "hugo.evers@gmail.com"

from .common import *

# from .database import PlanningSessionService, ReminderService, UserPreferencesService

# from .haunter_bot import HaunterBot  # TODO: Fix imports
# from .calendar_watch_server import CalendarWatchServer  # TODO: Fix imports
# from .models import PlanningSession, Reminder, UserPreferences
from .planner_bot import PlannerBot

__all__ = [
    "PlannerBot",
    # "HaunterBot",  # TODO: Fix imports
    # "CalendarWatchServer",  # TODO: Fix imports
    # "PlanningSession",
    # "Reminder",
    # "UserPreferences",
    # "PlanningSessionService",
    # "ReminderService",
    # "UserPreferencesService",
]

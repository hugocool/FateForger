"""
Productivity Bot - Does the productivity for you.

A Slack bot that helps with planning, reminders, and calendar management.
"""

__version__ = "0.1.0"
__author__ = "hugocool"
__email__ = "hugo.evers@gmail.com"

from .common import *
from .planner_bot import PlannerBot
from .haunter_bot import HaunterBot
from .calendar_watch_server import CalendarWatchServer

__all__ = ["PlannerBot", "HaunterBot", "CalendarWatchServer"]

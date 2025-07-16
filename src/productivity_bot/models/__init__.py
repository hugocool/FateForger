"""
Models package for structured data representations.

This package contains Pydantic models for various data structures
used throughout the productivity bot, including planner actions
and other structured representations.
"""

# Import Pydantic models from the models subdirectory
from .planner_action import PlannerAction

__all__ = [
    "PlannerAction",
]

"""
Haunting subsystem for productivity bot.

This module provides the infrastructure for haunting agents that follow up
with users using exponential back-off and APScheduler integration.
"""

from .base_haunter import BaseHaunter
from .bootstrap_haunter import PlanningBootstrapHaunter

__all__ = ["BaseHaunter", "PlanningBootstrapHaunter"]

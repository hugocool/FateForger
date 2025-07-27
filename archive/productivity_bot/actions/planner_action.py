"""Planner action utilities and system prompt."""

from jinja2 import Template

from ..intent_models import PlannerAction
from ..prompt_templates import PLANNER_TEMPLATE


def get_planner_system_message() -> str:
    """Render the planner system message with the PlannerAction schema."""
    return Template(PLANNER_TEMPLATE).render(schema=PlannerAction.model_json_schema())


PLANNER_SYSTEM_MESSAGE = get_planner_system_message()

__all__ = ["PlannerAction", "PLANNER_SYSTEM_MESSAGE", "get_planner_system_message"]

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.admonisher.agent import ADMONISHER_PROMPT
from fateforger.agents.receptionist.agent import RECEPTIONIST_PROMPT
from fateforger.agents.revisor.agent import REVISOR_PROMPT


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def test_admonisher_prompt_routes_calendar_check_to_planner():
    prompt = _norm(ADMONISHER_PROMPT)
    assert "is a timeboxing session planned for tomorrow?" in prompt
    assert "calendar inspection and hand off to `planner_agent`" in prompt
    assert "not `timeboxing_agent`" in prompt


def test_receptionist_prompt_routes_calendar_check_to_planner():
    prompt = _norm(RECEPTIONIST_PROMPT)
    assert "is a timeboxing session planned for tomorrow?" in prompt
    assert "calendar inspection and hand off to `planner_agent`" in prompt
    assert "not `timeboxing_agent`" in prompt


def test_receptionist_prompt_keeps_schedule_building_on_timeboxing():
    prompt = _norm(RECEPTIONIST_PROMPT)
    assert "concrete schedule for a day" in prompt
    assert "hand off to `timeboxing_agent`" in prompt


def test_receptionist_prompt_routes_sprint_execution_to_tasks():
    prompt = _norm(RECEPTIONIST_PROMPT)
    assert "sprint/backlog refinement" in prompt
    assert "patching notion sprint page content" in prompt
    assert "hand off to `tasks_agent`" in prompt


def test_admonisher_prompt_routes_sprint_execution_to_tasks():
    prompt = _norm(ADMONISHER_PROMPT)
    assert "sprint/backlog refinement" in prompt
    assert "hand off to `tasks_agent`" in prompt


def test_revisor_prompt_routes_sprint_execution_to_tasks():
    prompt = _norm(REVISOR_PROMPT)
    assert "operational sprint/backlog execution" in prompt
    assert "hand off to `tasks_agent`" in prompt

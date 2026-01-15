from typing import Any, List

import pytest

pytest.importorskip("trustcall")
pytest.importorskip("langchain_core.messages")

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field
from trustcall import create_extractor


class Event(BaseModel):
    summary: str
    start_time: str = Field(description="HH:MM")
    end_time: str = Field(description="HH:MM")


class Timebox(BaseModel):
    events: List[Event]
    date: str
    timezone: str


class DummyLLM:
    """Minimal stand-in for a chat model that always returns the same AIMessage."""

    def __init__(self, response: AIMessage):
        self.response = response

    def bind_tools(self, tools, tool_choice=None, **kwargs):  # noqa: D401
        # Trustcall only needs an object with .invoke/.ainvoke, so return self.
        return self

    def invoke(self, messages, config=None, **kwargs):  # noqa: D401
        return self.response

    async def ainvoke(self, messages, config=None, **kwargs):  # noqa: D401
        return self.response


def test_trustcall_applies_json_patch():
    existing_timebox = {
        "events": [
            {"summary": "Team sync", "start_time": "10:00", "end_time": "10:30"},
            {"summary": "Deep work", "start_time": "10:30", "end_time": "11:30"},
        ],
        "date": "2024-10-01",
        "timezone": "UTC",
    }

    ai_patch_response = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_patch",
                "name": "PatchDoc",
                "args": {
                    "json_doc_id": "Timebox",
                    "planned_edits": "Move team sync to 11:00 and shift following events.",
                    "patches": [
                        {"op": "replace", "path": "/events/0/start_time", "value": "11:00"},
                        {"op": "replace", "path": "/events/0/end_time", "value": "11:30"},
                        {"op": "replace", "path": "/events/1/start_time", "value": "11:30"},
                        {"op": "replace", "path": "/events/1/end_time", "value": "12:30"},
                    ],
                },
            }
        ],
    )

    llm = DummyLLM(ai_patch_response)

    extractor = create_extractor(
        llm,
        tools=[Timebox],
        tool_choice="Timebox",
        enable_inserts=False,
        enable_deletes=False,
    )

    result = extractor.invoke(
        {
            "messages": "Move the team sync from 10 AM to 11 AM and shift all downstream events.",
            "existing": {"Timebox": existing_timebox},
        }
    )

    patched: Timebox = result["responses"][0]

    assert patched.events[0].start_time == "11:00"
    assert patched.events[0].end_time == "11:30"
    assert patched.events[1].start_time == "11:30"
    assert patched.events[1].end_time == "12:30"


if __name__ == "__main__":
    # Allow running this file directly without pytest/conftest wiring.
    test_trustcall_applies_json_patch()
    print("trustcall patch application test passed.")

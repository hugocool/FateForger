from __future__ import annotations

from types import SimpleNamespace

from fateforger.agents.timeboxing.nlu import ConstraintInterpretation
from fateforger.agents.timeboxing.pydantic_parsing import parse_chat_content
from fateforger.agents.timeboxing.stage_gating import StageGateOutput


def test_parse_chat_content_accepts_json_string_payload() -> None:
    response = SimpleNamespace(
        chat_message=SimpleNamespace(
            content=(
                '{"stage_id":"CollectConstraints","ready":false,'
                '"summary":["Anchored sleep"],"missing":["work window"],'
                '"question":"What work window should we use?",'
                '"facts":{"timezone":"Europe/Amsterdam"},'
                '"response_message":{"sections":[{"kind":"next_steps",'
                '"heading":"What I need from you","content":["Share work window"]}]}}'
            )
        )
    )

    parsed = parse_chat_content(StageGateOutput, response)
    assert parsed.stage_id.value == "CollectConstraints"
    assert parsed.ready is False
    assert parsed.response_message is not None
    assert parsed.response_message.sections[0].kind == "next_steps"


def test_parse_chat_content_accepts_fenced_json_payload() -> None:
    response = SimpleNamespace(
        chat_message=SimpleNamespace(
            content=(
                "```json\n"
                '{"stage_id":"CaptureInputs","ready":true,"summary":["Ready"],'
                '"missing":[],"question":null,"facts":{"daily_one_thing":"Taxes"},'
                '"response_message":{"sections":[]}}\n'
                "```"
            )
        )
    )

    parsed = parse_chat_content(StageGateOutput, response)
    assert parsed.stage_id.value == "CaptureInputs"
    assert parsed.ready is True


def test_parse_chat_content_accepts_json_wrapped_in_text_prefix() -> None:
    response = SimpleNamespace(
        chat_message=SimpleNamespace(
            content=(
                "Here is the structured output:\\n"
                '{"should_extract":true,"scope":"session","constraints":[],'
                '"start_date":null,"end_date":null,"language":null,'
                '"explanation":"User stated actionable planning constraints."}'
            )
        )
    )

    parsed = parse_chat_content(ConstraintInterpretation, response)
    assert parsed.should_extract is True
    assert parsed.scope == "session"
    assert parsed.constraints == []


def test_parse_chat_content_accepts_double_encoded_json_string() -> None:
    response = SimpleNamespace(
        chat_message=SimpleNamespace(
            content=(
                '"{\\"should_extract\\":true,\\"scope\\":\\"session\\",'
                '\\"constraints\\":[],\\"start_date\\":null,\\"end_date\\":null,'
                '\\"language\\":null,\\"explanation\\":\\"Double encoded\\"}"'
            )
        )
    )

    parsed = parse_chat_content(ConstraintInterpretation, response)
    assert parsed.should_extract is True
    assert parsed.scope == "session"

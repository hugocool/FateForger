"""#254: every schema-bound turn logged a Pydantic serializer warning.

The warning is real and the type really is wrong -- but not in this code.
``openai.lib._parsing._completions.parse_chat_completion`` constructs
``ParsedChatCompletion[ResponseFormatT]`` with the TypeVar unsolved, so the
``parsed`` field's serializer is typed for ``None``; AutoGen then
``model_dump()``s that object for its ``LLMCallEvent``. Verified against
openai 2.54.0 (installed) and 3.7.0 (latest at 2026-09-02): both build the
same generic. Nothing this repository writes into ``parsed`` is at fault.

So the filter in ``fateforger.llm.factory`` is scoped to exactly that
warning and nothing else, and this file keeps two facts honest:

* with the filter installed, a schema-bound completion dumps cleanly;
* without it, the warning still fires -- when this second test starts
  failing, upstream has fixed the type and the filter should be deleted.
"""

from __future__ import annotations

import json
import warnings

import pytest
from openai.lib._parsing._completions import parse_chat_completion
from openai.types.chat import ChatCompletion

from fateforger.llm.factory import suppress_upstream_parsed_typevar_warning
from fateforger.slack_bot.timeboxing_intents import InterpretedTimeboxTurn


def _parsed_completion():
    raw = ChatCompletion.model_validate(
        {
            "id": "cmpl",
            "object": "chat.completion",
            "created": 0,
            "model": "stub",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"decision": "advance"}),
                    },
                }
            ],
        }
    )
    return parse_chat_completion(
        response_format=InterpretedTimeboxTurn, chat_completion=raw, input_tools=[]
    )


def _dump_and_collect(*, install_filter: bool) -> list[warnings.WarningMessage]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if install_filter:
            suppress_upstream_parsed_typevar_warning()
        _parsed_completion().model_dump()
    return list(caught)


def test_a_schema_bound_completion_dumps_without_a_warning_once_the_filter_is_on():
    assert _dump_and_collect(install_filter=True) == []


def test_the_upstream_type_is_still_wrong_so_the_filter_is_still_needed():
    """Control. If this fails, openai has solved the TypeVar: delete
    ``suppress_upstream_parsed_typevar_warning`` and this file."""
    caught = _dump_and_collect(install_filter=False)
    assert len(caught) == 1
    assert issubclass(caught[0].category, UserWarning)
    assert "field_name='parsed'" in str(caught[0].message)


def test_an_unrelated_pydantic_serializer_warning_still_surfaces():
    """The filter must not become a blanket mute on pydantic."""
    from pydantic import BaseModel

    class Narrow(BaseModel):
        count: int

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        suppress_upstream_parsed_typevar_warning()
        Narrow.model_construct(count="not an int").model_dump()
    assert len(caught) == 1
    assert "field_name='count'" in str(caught[0].message)

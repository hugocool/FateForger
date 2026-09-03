"""#257: notion-mcp registration failed every startup with
``TypeError: unhashable type: 'list'``.

Six of notion-mcp's 19 tools (``API-patch-page``, ``API-create-a-database``,
...) describe ``rich_text[].text.link`` as ``{"type": ["object", "null"]}``
-- JSON Schema's shorthand for a nullable object. autogen_core 0.7.5's
``_json_to_pydantic`` does ``json_type not in TYPE_MAPPING`` on that list
and dies; 0.7.5 is also the latest release at 2026-09-02, so the converter
has to be fed a shape it understands. The loader below rewrites the
shorthand before the adapter sees it, and the probe tells a crash inside
our client apart from a server that is not there.
"""

from __future__ import annotations

import copy
import logging

import pytest
from autogen_core.utils import schema_to_pydantic_model

from fateforger.core import runtime as runtime_module
from fateforger.tools.mcp_tool_schemas import normalise_nullable_types

# Trimmed from notion-mcp's live ``API-patch-page`` schema.
NOTION_LIKE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string"},
        "properties": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "link": {
                                        "type": ["object", "null"],
                                        "properties": {"url": {"type": "string"}},
                                        "required": ["url"],
                                    },
                                },
                                "required": ["content", "link"],
                            }
                        },
                    },
                }
            },
        },
    },
    "required": ["page_id"],
}


def test_the_live_notion_shape_still_breaks_the_converter_unnormalised():
    """Control: if this passes one day, autogen learned the shorthand and
    ``normalise_nullable_types`` can be retired."""
    with pytest.raises(TypeError):
        schema_to_pydantic_model(copy.deepcopy(NOTION_LIKE_SCHEMA))


def test_a_nullable_object_becomes_an_optional_object_the_converter_accepts():
    normalised = normalise_nullable_types(copy.deepcopy(NOTION_LIKE_SCHEMA))
    model = schema_to_pydantic_model(normalised)
    text_schema = normalised["properties"]["properties"]["properties"]["title"]["items"][
        "properties"
    ]["text"]
    assert text_schema["properties"]["link"]["type"] == "object"
    # Nullable means "may be absent"; the converter only knows Optional via
    # not-required, so the key leaves ``required``.
    assert text_schema["required"] == ["content"]
    assert model.model_json_schema()["required"] == ["page_id"]


def test_normalisation_does_not_touch_the_caller_s_schema():
    original = copy.deepcopy(NOTION_LIKE_SCHEMA)
    normalise_nullable_types(original)
    assert original == NOTION_LIKE_SCHEMA


def test_a_union_of_two_real_types_is_left_alone():
    """``["string", "integer"]`` is not nullability; there is nothing honest
    to rewrite it to, so it stays and the converter's refusal surfaces."""
    schema = {"type": "object", "properties": {"n": {"type": ["string", "integer"]}}}
    assert normalise_nullable_types(schema)["properties"]["n"]["type"] == [
        "string",
        "integer",
    ]


def test_a_bare_null_list_collapses_to_null():
    schema = {"type": "object", "properties": {"n": {"type": ["null"]}}}
    assert normalise_nullable_types(schema)["properties"]["n"]["type"] == "null"


# ---------------------------------------------------------------------------
# The probe: a crash in our own client is not "the server is down".
# ---------------------------------------------------------------------------


def _servers():
    return [
        runtime_module._McpStartupServer(
            name="notion-mcp", url="http://notion:3001/mcp", optional=True
        ),
    ]


async def test_a_crash_inside_tool_discovery_is_logged_as_an_error_with_traceback(
    monkeypatch, caplog
):
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", _servers)

    async def _fake_discover(*, url, headers, timeout_s):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)
    with caplog.at_level(logging.WARNING, logger="fateforger.core.runtime"):
        await runtime_module._assert_mcp_servers_available()

    (record,) = [r for r in caplog.records if "notion-mcp" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and record.exc_info[0] is TypeError
    assert "raised" in record.getMessage()


async def test_an_unreachable_optional_server_stays_a_warning_without_traceback(
    monkeypatch, caplog
):
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", _servers)

    async def _fake_discover(*, url, headers, timeout_s):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)
    with caplog.at_level(logging.WARNING, logger="fateforger.core.runtime"):
        await runtime_module._assert_mcp_servers_available()

    (record,) = [r for r in caplog.records if "notion-mcp" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "unavailable" in record.getMessage()


async def test_a_group_of_connection_failures_is_still_connectivity(monkeypatch, caplog):
    """anyio task groups wrap transport errors in ExceptionGroup."""
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", _servers)

    async def _fake_discover(*, url, headers, timeout_s):
        raise ExceptionGroup("boom", [ConnectionError("refused"), TimeoutError()])

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)
    with caplog.at_level(logging.WARNING, logger="fateforger.core.runtime"):
        await runtime_module._assert_mcp_servers_available()

    (record,) = [r for r in caplog.records if "notion-mcp" in r.getMessage()]
    assert record.levelno == logging.WARNING

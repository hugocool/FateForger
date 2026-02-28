"""Unit tests for Prometheus-oriented observability hooks."""

from __future__ import annotations

import logging

import fateforger.core.logging_config as logging_config


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _histogram_sample(metric, sample_suffix: str, labels: dict[str, str]) -> float:
    sample_name = f"{metric._name}_{sample_suffix}"
    for family in metric.collect():
        for sample in family.samples:
            if sample.name == sample_name and sample.labels == labels:
                return float(sample.value)
    return 0.0


class TestSanitizeAgentLabel:
    """_sanitize_agent_label should strip high-cardinality suffixes."""

    def test_slash_format_strips_session(self) -> None:
        result = logging_config._sanitize_agent_label(
            "timeboxing_agent/C0AA6HC1RJL:1772248936.310119"
        )
        assert result == "timeboxing_agent"

    def test_underscore_session_suffix_stripped(self) -> None:
        result = logging_config._sanitize_agent_label(
            "timeboxing_agent_C0AA6HC1RJL:1772290041.386259"
        )
        assert result == "timeboxing_agent"

    def test_revisor_underscore_session_suffix_stripped(self) -> None:
        result = logging_config._sanitize_agent_label(
            "revisor_agent_C0A9R6GBJRF:1772282202.203419"
        )
        assert result == "revisor_agent"

    def test_uuid_suffix_stripped(self) -> None:
        result = logging_config._sanitize_agent_label(
            "TurnInitNode_7f1fc69d-15e3-4d21-a70b-03d73b299357"
        )
        assert result == "TurnInitNode"

    def test_double_uuid_suffix_stripped(self) -> None:
        result = logging_config._sanitize_agent_label(
            "TurnInitNode_7f1fc69d-15e3-4d21-a70b-03d73b299357_7f1fc69d-15e3-4d21-a70b-03d73b299357"
        )
        assert result == "TurnInitNode"

    def test_stage_node_maps_to_stage_name(self) -> None:
        result = logging_config._sanitize_agent_label(
            "StageCollectConstraintsNode_7f1fc69d-15e3-4d21-a70b-03d73b299357"
        )
        assert result == "CollectConstraints"

    def test_stable_name_unchanged(self) -> None:
        assert logging_config._sanitize_agent_label("revisor_agent") == "revisor_agent"
        assert (
            logging_config._sanitize_agent_label("timeboxing_agent")
            == "timeboxing_agent"
        )

    def test_none_returns_none(self) -> None:
        assert logging_config._sanitize_agent_label(None) is None
        assert logging_config._sanitize_agent_label("") is None


class TestExtractContextFromAgentIdUnderscoreFormat:
    """_extract_context_from_agent_id should handle underscore-suffix format."""

    def test_slash_format_still_works(self) -> None:
        sk, ch, ts = logging_config._extract_context_from_agent_id(
            "timeboxing_agent/C0AA6HC1RJL:1772248936.310119"
        )
        assert sk == "C0AA6HC1RJL:1772248936.310119"
        assert ch == "C0AA6HC1RJL"
        assert ts == "1772248936.310119"

    def test_underscore_format_extracts_context(self) -> None:
        sk, ch, ts = logging_config._extract_context_from_agent_id(
            "timeboxing_agent_C0AA6HC1RJL:1772290041.386259"
        )
        assert sk == "C0AA6HC1RJL:1772290041.386259"
        assert ch == "C0AA6HC1RJL"
        assert ts == "1772290041.386259"

    def test_no_suffix_returns_none(self) -> None:
        sk, ch, ts = logging_config._extract_context_from_agent_id("timeboxing_agent")
        assert sk is None and ch is None and ts is None

    def test_uuid_agent_no_context(self) -> None:
        sk, ch, ts = logging_config._extract_context_from_agent_id(
            "TurnInitNode_7f1fc69d-15e3-4d21-a70b-03d73b299357"
        )
        assert sk is None and ch is None and ts is None


def test_prometheus_exporter_start_is_idempotent(monkeypatch) -> None:
    """Exporter bootstrap should start once for repeated setup calls."""
    calls: list[int] = []

    monkeypatch.setenv("OBS_PROMETHEUS_ENABLED", "1")
    monkeypatch.setenv("OBS_PROMETHEUS_PORT", "8123")
    monkeypatch.setattr(logging_config, "_PROM_STARTED_PORT", None)
    monkeypatch.setattr(logging_config, "start_http_server", calls.append)

    logging_config._configure_prometheus_exporter()
    logging_config._configure_prometheus_exporter()

    assert calls == [8123]


def test_record_observability_event_updates_metrics(monkeypatch) -> None:
    """LLM/tool/error events should update counters with bounded labels."""
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    logging_config._ensure_metrics_initialized()

    call_labels = {
        "agent": "timeboxing_agent",
        "model": "google_gemini-3",
        "status": "ok",
        "call_label": "planning_date",
        "function": "planning_date",
    }
    token_labels = {
        "agent": "timeboxing_agent",
        "model": "google_gemini-3",
        "type": "prompt",
        "call_label": "planning_date",
        "function": "planning_date",
    }
    tool_labels = {
        "agent": "timeboxing_agent",
        "tool": "list-events",
        "status": "ok",
    }
    error_labels = {
        "component": "planner_agent",
        "error_type": "MessageHandlerException",
    }

    calls_before = _counter_value(logging_config._METRIC_LLM_CALLS, **call_labels)
    tokens_before = _counter_value(logging_config._METRIC_LLM_TOKENS, **token_labels)
    tools_before = _counter_value(logging_config._METRIC_TOOL_CALLS, **tool_labels)
    errors_before = _counter_value(logging_config._METRIC_ERRORS, **error_labels)

    logging_config._record_observability_event(
        {
            "type": "LLMCall",
            "agent_id": "timeboxing_agent",
            "call_label": "planning_date",
            "model": "google/gemini-3",
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "response": {"model": "google/gemini-3"},
            "messages": [{"role": "user", "content": "hello"}],
        },
        record_level=logging.INFO,
    )
    logging_config._record_observability_event(
        {
            "type": "ToolCall",
            "agent_id": "timeboxing_agent",
            "tool_name": "list-events",
        },
        record_level=logging.INFO,
    )
    logging_config._record_observability_event(
        {
            "type": "MessageHandlerException",
            "handling_agent": "planner_agent",
            "exception": "invalid_date",
        },
        record_level=logging.ERROR,
    )

    assert (
        _counter_value(logging_config._METRIC_LLM_CALLS, **call_labels)
        == calls_before + 1
    )
    assert (
        _counter_value(logging_config._METRIC_LLM_TOKENS, **token_labels)
        == tokens_before + 123
    )
    assert (
        _counter_value(logging_config._METRIC_TOOL_CALLS, **tool_labels)
        == tools_before + 1
    )
    assert (
        _counter_value(logging_config._METRIC_ERRORS, **error_labels)
        == errors_before + 1
    )


def test_observe_stage_duration_records_histogram() -> None:
    """Stage duration helper should write one histogram sample."""
    logging_config._ensure_metrics_initialized()
    stage = "collect_constraints"
    labels = {"stage": stage}
    before = _histogram_sample(
        logging_config._METRIC_STAGE_DURATION,
        sample_suffix="count",
        labels=labels,
    )

    logging_config.observe_stage_duration(stage=stage, duration_s=1.25)

    after = _histogram_sample(
        logging_config._METRIC_STAGE_DURATION,
        sample_suffix="count",
        labels=labels,
    )
    assert after == before + 1


def test_record_observability_event_derives_timeboxing_context(monkeypatch) -> None:
    """LLM audit events should derive session/thread/channel from agent key when omitted."""
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    captured: list[dict] = []
    monkeypatch.setattr(logging_config, "_emit_llm_audit_event", captured.append)

    logging_config._record_observability_event(
        {
            "type": "LLMCall",
            "agent_id": "timeboxing_agent/C0AA6HC1RJL:1772248936.310119",
            "response": {"model": "google/gemini-3-flash-preview"},
            "messages": [{"role": "user", "content": "start"}],
        },
        record_level=logging.INFO,
    )

    assert captured
    event = captured[-1]
    assert event["session_key"] == "C0AA6HC1RJL:1772248936.310119"
    assert event["channel_id"] == "C0AA6HC1RJL"
    assert event["thread_ts"] == "1772248936.310119"
    assert event["model"] == "google_gemini-3-flash-preview"
    assert event["call_label"] == "timeboxing_agent"
    assert event["function"] == "timeboxing_agent"


def test_record_observability_event_derives_stage_call_label(monkeypatch) -> None:
    """Stage node agent ids should map to stable stage-level call labels."""
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    captured: list[dict] = []
    monkeypatch.setattr(logging_config, "_emit_llm_audit_event", captured.append)

    logging_config._record_observability_event(
        {
            "type": "LLMCall",
            "agent_id": "StageCollectConstraintsNode_abc/abc",
            "response": {"model": "google/gemini-3-flash-preview"},
            "messages": [{"role": "user", "content": "start"}],
        },
        record_level=logging.INFO,
    )

    assert captured
    assert captured[-1]["call_label"] == "CollectConstraints"
    assert captured[-1]["function"] == "CollectConstraints"


def test_record_observability_event_sanitizes_high_cardinality_agent_label(
    monkeypatch,
) -> None:
    """High-cardinality agent_ids should be sanitized before Prometheus emission."""
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    logging_config._ensure_metrics_initialized()

    # Agent ID in real runtime format: timeboxing_agent_CHANID:thread_ts
    high_card_agent_id = "timeboxing_agent_C0AA6HC1RJL:1772290041.386259"
    # Expected low-cardinality label value
    sanitized_label = "timeboxing_agent"

    stable_call_labels = {
        "agent": sanitized_label,
        "model": "google_gemini-3-flash-preview",
        "status": "ok",
        "call_label": sanitized_label,
        "function": sanitized_label,
    }
    calls_before = _counter_value(
        logging_config._METRIC_LLM_CALLS, **stable_call_labels
    )

    logging_config._record_observability_event(
        {
            "type": "LLMCall",
            "agent_id": high_card_agent_id,
            "response": {"model": "google/gemini-3-flash-preview"},
            "messages": [{"role": "user", "content": "hello"}],
        },
        record_level=logging.INFO,
    )

    # Metric must land on the sanitized label, not the raw high-cardinality one
    assert (
        _counter_value(logging_config._METRIC_LLM_CALLS, **stable_call_labels)
        == calls_before + 1
    )


def test_record_observability_event_sanitizes_uuid_node_agent_label(
    monkeypatch,
) -> None:
    """UUID-suffixed node agent_ids should emit stable TurnInitNode / DecisionNode labels."""
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")
    logging_config._ensure_metrics_initialized()

    uuid_agent_id = (
        "TurnInitNode_7f1fc69d-15e3-4d21-a70b-03d73b299357"
        "_7f1fc69d-15e3-4d21-a70b-03d73b299357"
    )
    stable_call_labels = {
        "agent": "TurnInitNode",
        "model": "google_gemini-3-flash-preview",
        "status": "ok",
        "call_label": "TurnInitNode",
        "function": "TurnInitNode",
    }
    calls_before = _counter_value(
        logging_config._METRIC_LLM_CALLS, **stable_call_labels
    )

    logging_config._record_observability_event(
        {
            "type": "LLMCall",
            "agent_id": uuid_agent_id,
            "response": {"model": "google/gemini-3-flash-preview"},
            "messages": [{"role": "user", "content": "hello"}],
        },
        record_level=logging.INFO,
    )

    assert (
        _counter_value(logging_config._METRIC_LLM_CALLS, **stable_call_labels)
        == calls_before + 1
    )

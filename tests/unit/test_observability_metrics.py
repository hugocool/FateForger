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
    }
    token_labels = {
        "agent": "timeboxing_agent",
        "model": "google_gemini-3",
        "type": "prompt",
        "call_label": "planning_date",
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

    assert _counter_value(logging_config._METRIC_LLM_CALLS, **call_labels) == calls_before + 1
    assert _counter_value(logging_config._METRIC_LLM_TOKENS, **token_labels) == tokens_before + 123
    assert _counter_value(logging_config._METRIC_TOOL_CALLS, **tool_labels) == tools_before + 1
    assert _counter_value(logging_config._METRIC_ERRORS, **error_labels) == errors_before + 1


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

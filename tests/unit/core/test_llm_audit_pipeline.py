from __future__ import annotations

import json
import queue
import time

import fateforger.core.logging_config as logging_config


class _FakeResponse:
    def __init__(self, status: int = 204) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _stop_audit_thread() -> None:
    thread = logging_config._LLM_AUDIT_THREAD
    if thread is None:
        return
    logging_config._LLM_AUDIT_STOP.set()
    thread.join(timeout=1.0)
    logging_config._LLM_AUDIT_THREAD = None
    logging_config._LLM_AUDIT_QUEUE = None
    logging_config._LLM_AUDIT_SINK = "off"


def test_llm_audit_pipeline_pushes_to_loki_async(monkeypatch) -> None:
    pushed_payloads: list[dict] = []

    def _fake_urlopen(req, timeout=0):
        pushed_payloads.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(204)

    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "1")
    monkeypatch.setenv("OBS_LLM_AUDIT_SINK", "loki")
    monkeypatch.setenv("OBS_LLM_AUDIT_MODE", "sanitized")
    monkeypatch.setenv("OBS_LLM_AUDIT_QUEUE_MAX", "128")
    monkeypatch.setenv("OBS_LLM_AUDIT_BATCH_SIZE", "16")
    monkeypatch.setenv("OBS_LLM_AUDIT_FLUSH_INTERVAL_MS", "20")
    monkeypatch.setenv(
        "OBS_LLM_AUDIT_LOKI_URL", "http://localhost:3100/loki/api/v1/push"
    )
    monkeypatch.setattr(logging_config.url_request, "urlopen", _fake_urlopen)

    _stop_audit_thread()
    try:
        logging_config._configure_llm_audit_pipeline()
        logging_config.emit_llm_audit_event(
            {
                "agent": "revisor_agent",
                "call_label": "weekly_review_intent",
                "function": "weekly_review_intent",
                "model": "google/gemini-2.5-pro",
                "status": "ok",
                "api_key": "abcd",
                "request_excerpt": "prompt payload",
                "response_excerpt": "hello",
            }
        )
        deadline = time.time() + 1.0
        while not pushed_payloads and time.time() < deadline:
            time.sleep(0.02)

        assert pushed_payloads, "expected background loki push"
        encoded_line = pushed_payloads[-1]["streams"][0]["values"][0][1]
        event = json.loads(encoded_line)
        assert event["agent"] == "revisor_agent"
        assert event["call_label"] == "weekly_review_intent"
        assert event["function"] == "weekly_review_intent"
        assert "***REDACTED***" in json.dumps(event)
    finally:
        _stop_audit_thread()


def test_llm_audit_queue_full_increments_drop_counter(monkeypatch) -> None:
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "1")
    monkeypatch.setenv("OBS_LLM_AUDIT_MODE", "sanitized")
    logging_config._ensure_metrics_initialized()

    logging_config._LLM_AUDIT_SINK = "loki"
    logging_config._LLM_AUDIT_QUEUE = queue.Queue(maxsize=1)
    logging_config._LLM_AUDIT_QUEUE.put({"first": 1})
    before = logging_config._METRIC_OBS_DROPPED.labels(
        source="llm_io", reason="queue_full"
    )._value.get()

    logging_config.emit_llm_audit_event(
        {
            "agent": "tasks_agent",
            "call_label": "task_refinement",
            "function": "task_refinement",
            "model": "google/gemini-2.5-pro",
            "status": "ok",
        }
    )

    after = logging_config._METRIC_OBS_DROPPED.labels(
        source="llm_io", reason="queue_full"
    )._value.get()
    assert after == before + 1

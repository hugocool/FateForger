import json
import logging
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fateforger.debug.log_index import append_index_entry

try:
    from prometheus_client import Counter, Histogram, start_http_server
except Exception:  # pragma: no cover - optional runtime dependency
    Counter = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]
    start_http_server = None  # type: ignore[assignment]


_PROM_LOCK = threading.Lock()
_PROM_STARTED_PORT: int | None = None
_METRICS_READY = False

_LLM_AUDIT_LOGGER_NAME = "fateforger.observability.llm_io"
_LLM_AUDIT_LOGGER: logging.Logger | None = None

_REDACT_KEYS = {
    "token",
    "authorization",
    "api_key",
    "secret",
    "password",
}
_NON_SECRET_TOKEN_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}

_METRIC_LLM_CALLS = None
_METRIC_LLM_TOKENS = None
_METRIC_TOOL_CALLS = None
_METRIC_ERRORS = None
_METRIC_STAGE_DURATION = None


def observe_stage_duration(*, stage: str, duration_s: float) -> None:
    """Observe stage duration in seconds (no-op when metrics are disabled)."""
    _ensure_metrics_initialized()
    metric = _METRIC_STAGE_DURATION
    if metric is None:
        return
    safe_stage = _bounded_label(stage, fallback="unknown")
    metric.labels(stage=safe_stage).observe(max(0.0, float(duration_s)))


def configure_logging(*, default_level: str | int = "INFO") -> None:
    """
    Configure application logging with sane defaults.

    Key behavior: reduce extremely large `autogen_core.events` INFO logs by default.
    """

    logging.basicConfig(level=_coerce_level(os.getenv("LOG_LEVEL", default_level)))
    _configure_prometheus_exporter()
    _configure_llm_audit_file_logging()

    mode = (os.getenv("AUTOGEN_EVENTS_LOG", "summary") or "summary").strip().lower()
    max_chars = _coerce_int(os.getenv("AUTOGEN_EVENTS_MAX_CHARS", "900"), default=900)
    max_tools = _coerce_int(os.getenv("AUTOGEN_EVENTS_MAX_TOOLS", "10"), default=10)

    autogen_logger = logging.getLogger("autogen_core.events")
    if not any(isinstance(f, _AutogenEventsFilter) for f in autogen_logger.filters):
        autogen_logger.addFilter(
            _AutogenEventsFilter(mode=mode, max_chars=max_chars, max_tools=max_tools)
        )

    core_logger = logging.getLogger("autogen_core")
    if not any(isinstance(f, _AutogenCoreFilter) for f in core_logger.filters):
        core_logger.addFilter(_AutogenCoreFilter(max_chars=max_chars))

    openai_client_logger = logging.getLogger("autogen_ext.models.openai._openai_client")
    if not any(
        isinstance(f, _SafeRecordMessageFilter) for f in openai_client_logger.filters
    ):
        openai_client_logger.addFilter(_SafeRecordMessageFilter())

    # Keep HTTP noise down by default (can still override via LOG_LEVEL).
    logging.getLogger("httpx").setLevel(
        _coerce_level(os.getenv("HTTPX_LOG_LEVEL", "WARNING"))
    )
    logging.getLogger("mcp").setLevel(_coerce_level(os.getenv("MCP_LOG_LEVEL", "INFO")))
    logging.getLogger("mcp.client.streamable_http").setLevel(
        _coerce_level(os.getenv("MCP_STREAMABLE_HTTP_LOG_LEVEL", "WARNING"))
    )
    logging.getLogger("apscheduler").setLevel(
        _coerce_level(os.getenv("APSCHEDULER_LOG_LEVEL", "WARNING"))
    )
    _configure_timebox_patcher_debug_file_logging()


def _configure_prometheus_exporter() -> None:
    if not _is_truthy(os.getenv("OBS_PROMETHEUS_ENABLED", "1")):
        return
    if start_http_server is None:
        logging.getLogger(__name__).warning(
            "prometheus_client unavailable; metrics exporter disabled"
        )
        return
    port = _coerce_int(os.getenv("OBS_PROMETHEUS_PORT", "9464"), default=9464)
    global _PROM_STARTED_PORT
    with _PROM_LOCK:
        if _PROM_STARTED_PORT == port:
            _ensure_metrics_initialized()
            return
        if _PROM_STARTED_PORT is not None and _PROM_STARTED_PORT != port:
            logging.getLogger(__name__).warning(
                "Prometheus exporter already running on port %s (requested %s).",
                _PROM_STARTED_PORT,
                port,
            )
            return
        try:
            start_http_server(port)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Prometheus exporter not started on :%s (%s).", port, exc
            )
            _PROM_STARTED_PORT = port
            _ensure_metrics_initialized()
            return
        _PROM_STARTED_PORT = port
        _ensure_metrics_initialized()
    logging.getLogger(__name__).info("Prometheus exporter enabled on :%s", port)


def _configure_llm_audit_file_logging() -> None:
    global _LLM_AUDIT_LOGGER
    if not _is_truthy(os.getenv("OBS_LLM_AUDIT_ENABLED", "1")):
        return
    if _LLM_AUDIT_LOGGER is not None and any(
        getattr(h, "_fftb_llm_io_file", False) for h in _LLM_AUDIT_LOGGER.handlers
    ):
        return
    log_dir = Path(os.getenv("OBS_LLM_AUDIT_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_path = log_dir / f"llm_io_{ts}_{os.getpid()}.jsonl"

    llm_logger = logging.getLogger(_LLM_AUDIT_LOGGER_NAME)
    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    setattr(handler, "_fftb_llm_io_file", True)
    llm_logger.addHandler(handler)
    llm_logger.setLevel(logging.INFO)
    llm_logger.propagate = False
    _LLM_AUDIT_LOGGER = llm_logger

    append_index_entry(
        index_path=log_dir / os.getenv("OBS_LLM_AUDIT_INDEX_FILE", "llm_io_index.jsonl"),
        entry={
            "type": "llm_io",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "log_path": str(file_path),
            "pid": os.getpid(),
            "mode": os.getenv("OBS_LLM_AUDIT_MODE", "sanitized"),
        },
    )
    logging.getLogger(__name__).info("LLM I/O audit logging enabled: %s", file_path)


def _configure_timebox_patcher_debug_file_logging() -> None:
    """Enable a dedicated patcher file logger when debug mode is enabled."""
    explicit = os.getenv("TIMEBOX_PATCHER_DEBUG_LOG")
    if explicit is None:
        enabled = _is_truthy(
            os.getenv("DEBUG") or os.getenv("FATEFORGER_DEBUG")
        ) or _debugger_attached()
    else:
        enabled = _is_truthy(explicit)
    if not enabled:
        return

    patcher_logger = logging.getLogger("fateforger.agents.timeboxing.patching")
    if any(getattr(h, "_fftb_patcher_file", False) for h in patcher_logger.handlers):
        return

    log_dir = Path(os.getenv("TIMEBOX_PATCHER_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = log_dir / f"timebox_patcher_{ts}_{os.getpid()}.log"

    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    setattr(handler, "_fftb_patcher_file", True)

    patcher_logger.addHandler(handler)
    # Keep patcher debug logs out of stdout noise; diagnostics go to file.
    patcher_logger.setLevel(logging.DEBUG)
    patcher_logger.propagate = False
    append_index_entry(
        index_path=log_dir / os.getenv("TIMEBOX_PATCHER_INDEX_FILE", "timebox_patcher_index.jsonl"),
        entry={
            "type": "timebox_patcher",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "logger": patcher_logger.name,
            "log_path": str(file_path),
            "pid": os.getpid(),
        },
    )
    logging.getLogger(__name__).info(
        "Timebox patcher debug logging enabled: %s", file_path
    )


def _coerce_level(value: str | int) -> int:
    if isinstance(value, int):
        return value
    name = (value or "").strip().upper()
    return getattr(logging, name, logging.INFO)


def _is_truthy(value: str | None) -> bool:
    """Interpret common truthy strings from environment variables."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "debug"}


def _debugger_attached() -> bool:
    """Return whether a debugger trace hook is attached."""
    try:
        return sys.gettrace() is not None
    except Exception:
        return False


def _coerce_int(value: str | None, *, default: int) -> int:
    try:
        if value is None:
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _ensure_metrics_initialized() -> None:
    global _METRICS_READY
    global _METRIC_LLM_CALLS, _METRIC_LLM_TOKENS, _METRIC_TOOL_CALLS
    global _METRIC_ERRORS, _METRIC_STAGE_DURATION

    if _METRICS_READY or Counter is None or Histogram is None:
        return
    _METRIC_LLM_CALLS = Counter(
        "fateforger_llm_calls_total",
        "LLM calls observed by logging pipeline",
        ["agent", "model", "status", "call_label"],
    )
    _METRIC_LLM_TOKENS = Counter(
        "fateforger_llm_tokens_total",
        "LLM token usage observed by logging pipeline",
        ["agent", "model", "type", "call_label"],
    )
    _METRIC_TOOL_CALLS = Counter(
        "fateforger_tool_calls_total",
        "Tool call outcomes observed by logging pipeline",
        ["agent", "tool", "status"],
    )
    _METRIC_ERRORS = Counter(
        "fateforger_errors_total",
        "Observed component errors",
        ["component", "error_type"],
    )
    _METRIC_STAGE_DURATION = Histogram(
        "fateforger_stage_duration_seconds",
        "Stage duration in seconds",
        ["stage"],
        buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    )
    _METRICS_READY = True


def _bounded_label(value: Any, *, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    compact = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)
    return compact[:80] or fallback


def _emit_llm_audit_event(event: dict[str, Any]) -> None:
    if not _is_truthy(os.getenv("OBS_LLM_AUDIT_ENABLED", "1")):
        return
    logger = _LLM_AUDIT_LOGGER
    if logger is None:
        return
    mode = (os.getenv("OBS_LLM_AUDIT_MODE", "sanitized") or "sanitized").strip().lower()
    max_chars = _coerce_int(os.getenv("OBS_LLM_AUDIT_MAX_CHARS", "2000"), default=2000)
    payload = dict(event)
    payload.setdefault("created_at", datetime.utcnow().isoformat() + "Z")
    serialized = (
        payload
        if mode in {"raw", "off"}
        else _sanitize_for_audit(payload, max_chars=max_chars)
    )
    logger.info(json.dumps(serialized, ensure_ascii=False, default=str))


def _sanitize_for_audit(value: Any, *, max_chars: int, key: str | None = None) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for child_key, child_value in value.items():
            child_key_str = str(child_key)
            if _key_is_sensitive(child_key_str):
                out[child_key_str] = "***REDACTED***"
                continue
            out[child_key_str] = _sanitize_for_audit(
                child_value, max_chars=max_chars, key=child_key_str
            )
        return out
    if isinstance(value, list):
        return [_sanitize_for_audit(item, max_chars=max_chars, key=key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_audit(item, max_chars=max_chars, key=key) for item in value]
    if key and _key_is_sensitive(key):
        return "***REDACTED***"
    if isinstance(value, str):
        return _truncate(value, max_chars=max_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(str(value), max_chars=max_chars)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in _NON_SECRET_TOKEN_KEYS:
        return False
    return any(marker in lowered for marker in _REDACT_KEYS)


def _extract_event_payload(msg_obj: Any, msg_text: str) -> dict[str, Any] | None:
    if isinstance(msg_obj, dict):
        return msg_obj
    kwargs = getattr(msg_obj, "kwargs", None)
    if isinstance(kwargs, dict):
        return kwargs
    if msg_text.startswith("{") and msg_text.endswith("}"):
        try:
            parsed = json.loads(msg_text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _extract_response_usage(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    usage = response.get("usage")
    if isinstance(usage, dict):
        return usage
    return {}


def _record_observability_event(payload: dict[str, Any], *, record_level: int) -> None:
    _ensure_metrics_initialized()
    event_type = str(payload.get("type", "")).strip()
    agent = _bounded_label(payload.get("agent_id"), fallback="unknown")
    call_label = _bounded_label(
        payload.get("call_label") or payload.get("stage"), fallback="unknown"
    )

    if event_type in {"LLMCall", "LLMStreamEnd"}:
        response = payload.get("response")
        model = _bounded_label(
            payload.get("model") or (response.get("model") if isinstance(response, dict) else None),
            fallback="unknown",
        )
        response_error = (
            isinstance(response, dict) and response.get("error") not in (None, "")
        )
        status = "error" if (record_level >= logging.ERROR or response_error) else "ok"
        if _METRIC_LLM_CALLS is not None:
            _METRIC_LLM_CALLS.labels(
                agent=agent,
                model=model,
                status=status,
                call_label=call_label,
            ).inc()
        prompt_tokens = payload.get("prompt_tokens")
        completion_tokens = payload.get("completion_tokens")
        usage = _extract_response_usage(response)
        if prompt_tokens is None:
            prompt_tokens = usage.get("prompt_tokens")
        if completion_tokens is None:
            completion_tokens = usage.get("completion_tokens")
        if _METRIC_LLM_TOKENS is not None:
            if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                _METRIC_LLM_TOKENS.labels(
                    agent=agent,
                    model=model,
                    type="prompt",
                    call_label=call_label,
                ).inc(prompt_tokens)
            if isinstance(completion_tokens, int) and completion_tokens >= 0:
                _METRIC_LLM_TOKENS.labels(
                    agent=agent,
                    model=model,
                    type="completion",
                    call_label=call_label,
                ).inc(completion_tokens)
        _emit_llm_audit_event(
            {
                "event_type": event_type,
                "agent": payload.get("agent_id"),
                "model": model,
                "status": status,
                "call_label": payload.get("call_label"),
                "stage": payload.get("stage"),
                "session_key": payload.get("session_key"),
                "thread_ts": payload.get("thread_ts"),
                "channel_id": payload.get("channel_id"),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "request_excerpt": payload.get("messages"),
                "response_excerpt": response,
                "error": response.get("error") if isinstance(response, dict) else None,
            }
        )
        return

    if event_type == "ToolCall":
        if _METRIC_TOOL_CALLS is not None:
            _METRIC_TOOL_CALLS.labels(
                agent=agent,
                tool=_bounded_label(payload.get("tool_name"), fallback="unknown"),
                status="ok" if record_level < logging.ERROR else "error",
            ).inc()
        return

    if event_type in {"MessageHandlerException", "AgentConstructionException"}:
        if _METRIC_ERRORS is not None:
            _METRIC_ERRORS.labels(
                component=_bounded_label(payload.get("handling_agent") or payload.get("agent_id"), fallback="unknown"),
                error_type=_bounded_label(payload.get("error_type") or event_type, fallback="error"),
            ).inc()
        _emit_llm_audit_event(
            {
                "event_type": event_type,
                "component": payload.get("handling_agent") or payload.get("agent_id"),
                "error": payload.get("exception"),
                "session_key": payload.get("session_key"),
                "thread_ts": payload.get("thread_ts"),
                "stage": payload.get("stage"),
            }
        )


class _AutogenEventsFilter(logging.Filter):
    def __init__(self, *, mode: str, max_chars: int, max_tools: int) -> None:
        super().__init__(name="autogen_core.events")
        self._mode = mode
        self._max_chars = max(200, max_chars)
        self._max_tools = max(1, max_tools)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "autogen_core.events":
            return True

        mode = self._mode
        raw_obj = record.msg
        msg = _safe_get_record_message(record)
        payload = _extract_event_payload(raw_obj, msg)
        if payload is not None:
            _record_observability_event(payload, record_level=record.levelno)
        if mode in ("off", "false", "0", "none"):
            return False
        record.msg = msg
        record.args = ()
        if mode in ("full", "on", "true", "1"):
            return True

        # "summary" (default)
        summarized = _summarize_autogen_event_message(
            msg, max_chars=self._max_chars, max_tools=self._max_tools
        )
        record.msg = summarized
        record.args = ()
        return True


class _AutogenCoreFilter(logging.Filter):
    # TODO(refactor,typed-events): Stop parsing plain-text log lines with regex.
    # Prefer structured logging payloads emitted by source logger adapters.
    _resolve_re = re.compile(
        r"^Resolving response with message type (?P<type>\w+)"
        r" for recipient (?P<recipient>.+?) from (?P<sender>[^:]+):"
    )

    def __init__(self, *, max_chars: int) -> None:
        super().__init__(name="autogen_core")
        self._max_chars = max(200, max_chars)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "autogen_core":
            return True

        msg = _safe_get_record_message(record)
        record.msg = msg
        record.args = ()
        m = self._resolve_re.match(msg)
        if not m:
            return True

        message_type = m.group("type")
        recipient = m.group("recipient").strip()
        sender = m.group("sender").strip()
        out = f"autogen_core resolved message_type={message_type} sender={sender} recipient={recipient}"
        record.msg = _truncate(out, max_chars=self._max_chars)
        record.args = ()
        return True


class _SafeRecordMessageFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """Coerce unserializable record messages into safe string payloads."""
        record.msg = _safe_get_record_message(record)
        record.args = ()
        return True


def _safe_get_record_message(record: logging.LogRecord) -> str:
    """Return a log message string without raising on unserializable payloads."""
    try:
        return record.getMessage()
    except Exception as exc:
        msg_obj = record.msg
        kwargs = getattr(msg_obj, "kwargs", None)
        if kwargs is not None:
            try:
                coerced = json.dumps(kwargs, default=str, ensure_ascii=False)
                return f"[coerced-log-payload:{type(exc).__name__}] {coerced}"
            except Exception:
                pass
        return (
            f"[coerced-log-payload:{type(exc).__name__}] "
            f"{type(msg_obj).__name__}: {repr(msg_obj)}"
        )


def _summarize_autogen_event_message(
    msg: str, *, max_chars: int, max_tools: int
) -> str:
    if not msg:
        return msg

    # TODO(refactor,typed-events): Remove substring probes for "tools"/"payload"
    # by relying on structured event objects instead of raw message text.
    if len(msg) <= max_chars and "\"tools\"" not in msg and "\"payload\"" not in msg:
        return msg

    payload: Any | None = None
    # TODO(refactor): Parse autogen event payloads with a Pydantic schema.
    if msg[:1] == "{" and msg[-1:] == "}":
        try:
            payload = json.loads(msg)
        except Exception:
            payload = None

    if not isinstance(payload, dict):
        return _truncate(msg, max_chars=max_chars)

    if "payload" in payload:
        return _summarize_autogen_message_event(payload, max_chars=max_chars)

    event_type = payload.get("type") or "event"
    agent_id = payload.get("agent_id")
    tools = payload.get("tools") or []
    tool_names = []
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") or {}
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str) and name:
                    tool_names.append(name)

    response = payload.get("response") or {}
    model = response.get("model") if isinstance(response, dict) else None
    usage = response.get("usage") if isinstance(response, dict) else None

    finish_reason = None
    tool_calls = []
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = first.get("finish_reason")
            message = first.get("message") if isinstance(first, dict) else None
            if isinstance(message, dict):
                tc = message.get("tool_calls") or []
                if isinstance(tc, list):
                    for call in tc:
                        if not isinstance(call, dict):
                            continue
                        fn = call.get("function") or {}
                        if isinstance(fn, dict):
                            name = fn.get("name")
                            if isinstance(name, str) and name:
                                tool_calls.append(name)

    summary_parts: list[str] = [f"autogen type={event_type}"]
    if isinstance(agent_id, str) and agent_id:
        summary_parts.append(f"agent_id={agent_id}")
    if isinstance(model, str) and model:
        summary_parts.append(f"model={model}")
    if finish_reason:
        summary_parts.append(f"finish={finish_reason}")

    if tool_calls:
        summary_parts.append(f"tool_calls={_format_list(tool_calls, max_items=5)}")

    if tool_names:
        summary_parts.append(
            f"tools={len(tool_names)} {_format_list(tool_names, max_items=max_tools)}"
        )

    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        if pt is not None or ct is not None or tt is not None:
            summary_parts.append(f"tokens={pt}/{ct}/{tt}")

    out = " ".join(summary_parts)
    return _truncate(out, max_chars=max_chars)


def _format_list(items: list[str], *, max_items: int) -> str:
    shown = items[: max(1, max_items)]
    suffix = ""
    if len(items) > len(shown):
        suffix = f", …+{len(items) - len(shown)}"
    return "(" + ", ".join(shown) + suffix + ")"


def _truncate(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 16)] + f" …(truncated,{len(value)})"


def _summarize_autogen_message_event(event: dict[str, Any], *, max_chars: int) -> str:
    # Example shape:
    # {"payload":"{...TextMessage...}","sender":"planner_agent/...","receiver":null,
    #  "kind":"MessageKind.RESPOND","delivery_stage":"DeliveryStage.SEND","type":"Message"}
    sender = event.get("sender")
    receiver = event.get("receiver")
    kind = event.get("kind")
    stage = event.get("delivery_stage")

    payload_raw = event.get("payload")
    payload_obj: Any | None = None
    # TODO(refactor): Validate payload with a structured Pydantic model.
    if isinstance(payload_raw, str) and payload_raw:
        try:
            payload_obj = json.loads(payload_raw)
        except Exception:
            payload_obj = None

    payload_type = None
    msg_id = None
    source = None
    content = None
    usage = None
    if isinstance(payload_obj, dict):
        payload_type = payload_obj.get("type")
        msg_id = payload_obj.get("id")
        source = payload_obj.get("source")
        content = payload_obj.get("content")
        usage = payload_obj.get("models_usage")

    parts: list[str] = ["autogen message"]
    if kind:
        parts.append(f"kind={kind}")
    if stage:
        parts.append(f"stage={stage}")
    if isinstance(sender, str) and sender:
        parts.append(f"sender={sender}")
    if receiver is None:
        parts.append("receiver=None")
    elif isinstance(receiver, str) and receiver:
        parts.append(f"receiver={receiver}")

    if payload_type:
        parts.append(f"payload_type={payload_type}")
    if isinstance(msg_id, str) and msg_id:
        parts.append(f"id={msg_id}")
    if isinstance(source, str) and source:
        parts.append(f"source={source}")

    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if pt is not None or ct is not None:
            parts.append(f"tokens={pt}/{ct}")

    if isinstance(content, str) and content:
        content_one_line = " ".join(content.splitlines())
        parts.append(f"content={_truncate(content_one_line, max_chars=220)}")

    return _truncate(" ".join(parts), max_chars=max_chars)

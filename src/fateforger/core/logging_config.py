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

_CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]+$")
_STAGE_AGENT_RE = re.compile(r"^Stage(?P<stage>[A-Za-z]+)Node(?:_|$)")


def observe_stage_duration(*, stage: str, duration_s: float) -> None:
    """Observe stage duration in seconds (no-op when metrics are disabled)."""
    _ensure_metrics_initialized()
    metric = _METRIC_STAGE_DURATION
    if metric is None:
        return
    safe_stage = _bounded_label(stage, fallback="unknown")
    metric.labels(stage=safe_stage).observe(max(0.0, float(duration_s)))


def record_llm_call(
    *,
    agent: str,
    model: str,
    status: str,
    call_label: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Increment LLM call + token counters from outside the AutoGen event pipeline.

    Use this for LLM calls that don't flow through autogen_core.events (e.g.
    direct OpenAI calls in utilities or MCP-facing code).
    """
    _ensure_metrics_initialized()
    safe_agent = _bounded_label(agent, fallback="unknown")
    safe_model = _bounded_label(model, fallback="unknown")
    safe_status = _bounded_label(status, fallback="unknown")
    safe_label = _bounded_label(call_label, fallback="unknown")
    if _METRIC_LLM_CALLS is not None:
        _METRIC_LLM_CALLS.labels(
            agent=safe_agent,
            model=safe_model,
            status=safe_status,
            call_label=safe_label,
        ).inc()
    if _METRIC_LLM_TOKENS is not None:
        if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
            _METRIC_LLM_TOKENS.labels(
                agent=safe_agent, model=safe_model, type="prompt", call_label=safe_label
            ).inc(prompt_tokens)
        if isinstance(completion_tokens, int) and completion_tokens >= 0:
            _METRIC_LLM_TOKENS.labels(
                agent=safe_agent,
                model=safe_model,
                type="completion",
                call_label=safe_label,
            ).inc(completion_tokens)


def record_tool_call(*, agent: str, tool: str, status: str) -> None:
    """Increment tool call counter from outside the AutoGen event pipeline.

    Use this for MCP/tool calls not observed via autogen_core.events.
    """
    _ensure_metrics_initialized()
    if _METRIC_TOOL_CALLS is None:
        return
    _METRIC_TOOL_CALLS.labels(
        agent=_bounded_label(agent, fallback="unknown"),
        tool=_bounded_label(tool, fallback="unknown"),
        status=_bounded_label(status, fallback="unknown"),
    ).inc()


def record_error(*, component: str, error_type: str) -> None:
    """Increment the error counter (no-op when metrics are disabled).

    Use this in any component (MCP clients, adapters, etc.) to surface errors
    to the fateforger_errors_total Prometheus counter.
    """
    _ensure_metrics_initialized()
    if _METRIC_ERRORS is None:
        return
    _METRIC_ERRORS.labels(
        component=_bounded_label(component, fallback="unknown"),
        error_type=_bounded_label(error_type, fallback="error"),
    ).inc()


def emit_llm_audit_event(event: dict[str, Any]) -> None:
    """Emit one structured LLM I/O audit event through the configured audit sink."""
    _emit_llm_audit_event(event)


# ---------------------------------------------------------------------------
# StructuredJsonFormatter
# ---------------------------------------------------------------------------

# Fields promoted from a JSON event payload into the top-level Loki envelope.
# Bounded / low-cardinality fields become Loki index labels (configured in
# promtail.yml).  High-cardinality fields stay as JSON keys queried with
# LogQL `| json`.
_STRUCTURED_EXTRACT_FIELDS: frozenset[str] = frozenset(
    {
        "type",  # → "event" in envelope
        "agent_id",  # → "agent" in envelope
        "stage",
        "model",
        "tool_name",
        "call_label",
        "session_key",
        "thread_ts",
        "channel_id",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as a normalized JSON envelope for Loki ingestion.

    Every line is a single JSON object with:
    - ``ts``       – RFC3339 UTC timestamp
    - ``level``    – lowercase level name
    - ``logger``   – logger name
    - ``message``  – human-readable message (or raw JSON if not an event)
    - Structured fields extracted from AutoGen event payloads (``event``,
      ``agent``, ``stage``, ``model``, ``tool_name``, ``session_key``, …)
    - ``exc``      – formatted exception traceback (when present)

    Sensitive keys (``api_key``, ``authorization``, ``secret``, …) are always
    redacted via :func:`_key_is_sensitive`.

    Enable for the root logger by setting ``OBS_LOG_FORMAT=json`` — done
    automatically by :func:`configure_logging`.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the log record as a JSON envelope string."""
        try:
            msg = record.getMessage()
        except Exception as exc:
            msg = f"[coerced-log-payload:{type(exc).__name__}] {record.msg!r}"

        ts = (
            datetime.utcfromtimestamp(record.created).isoformat(timespec="milliseconds")
            + "Z"
        )

        envelope: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": msg,
        }

        # Try to extract structured fields from JSON message payloads
        # (AutoGen events, structured log records, etc.)
        if msg and msg[0] == "{":
            try:
                payload = json.loads(msg)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                self._extract_into_envelope(envelope, payload)
                # Re-serialize message with sensitive keys stripped so the raw
                # message field in Loki never stores secrets.
                envelope["message"] = json.dumps(
                    self._redact_dict(payload), ensure_ascii=False, default=str
                )

        # Also check record.__dict__ extras (set via logger.extra={...})
        for field in _STRUCTURED_EXTRACT_FIELDS:
            target = (
                "event"
                if field == "type"
                else ("agent" if field == "agent_id" else field)
            )
            if target not in envelope:
                val = getattr(record, field, None)
                if val is not None:
                    envelope[target] = str(val)

        if record.exc_info:
            envelope["exc"] = self.formatException(record.exc_info)

        return json.dumps(envelope, ensure_ascii=False, default=str)

    @staticmethod
    def _extract_into_envelope(
        envelope: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        """Extract structured fields from an AutoGen event payload dict."""
        field_map = {
            "type": "event",
            "agent_id": "agent",
            "stage": "stage",
            "model": "model",
            "tool_name": "tool_name",
            "call_label": "call_label",
            "session_key": "session_key",
            "thread_ts": "thread_ts",
            "channel_id": "channel_id",
        }
        for src_key, dest_key in field_map.items():
            if dest_key in envelope:
                continue  # don't overwrite already-set fields
            val = payload.get(src_key)
            if val is None:
                continue
            # Redact sensitive source keys — shouldn't appear here in
            # practice, but guard anyway.
            if _key_is_sensitive(src_key):
                continue
            envelope[dest_key] = val

        # Redact any sensitive top-level keys that leak into the payload
        for key in list(payload.keys()):
            if _key_is_sensitive(key) and key not in envelope:
                # Don't add sensitive fields to the envelope at all.
                pass

    @staticmethod
    def _redact_dict(payload: dict[str, Any]) -> dict[str, Any]:
        """Return a shallow copy of *payload* with sensitive keys replaced by ``"[REDACTED]"``."""
        return {
            k: "[REDACTED]" if _key_is_sensitive(k) else v for k, v in payload.items()
        }


def configure_logging(*, default_level: str | int = "INFO") -> None:
    """
    Configure application logging with sane defaults.

    Key behavior: reduce extremely large `autogen_core.events` INFO logs by default.
    """

    logging.basicConfig(level=_coerce_level(os.getenv("LOG_LEVEL", default_level)))
    _configure_prometheus_exporter()
    _configure_json_stdout()
    _configure_llm_audit_file_logging()

    mode = _coerce_autogen_events_mode(os.getenv("AUTOGEN_EVENTS_LOG", "summary"))
    output_target = _coerce_autogen_events_output_target(
        os.getenv("AUTOGEN_EVENTS_OUTPUT_TARGET", "stdout")
    )
    full_payload_mode = _coerce_autogen_events_full_payload_mode(
        os.getenv("AUTOGEN_EVENTS_FULL_PAYLOAD_MODE", "sanitized")
    )
    max_chars = _coerce_int(os.getenv("AUTOGEN_EVENTS_MAX_CHARS", "900"), default=900)
    max_tools = _coerce_int(os.getenv("AUTOGEN_EVENTS_MAX_TOOLS", "10"), default=10)

    autogen_logger = logging.getLogger("autogen_core.events")
    if not any(isinstance(f, _AutogenEventsFilter) for f in autogen_logger.filters):
        autogen_logger.addFilter(
            _AutogenEventsFilter(
                mode=mode,
                max_chars=max_chars,
                max_tools=max_tools,
                output_target=output_target,
                full_payload_mode=full_payload_mode,
            )
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


def _configure_json_stdout() -> None:
    """Replace root stream handler formatter with StructuredJsonFormatter.

    Activated when ``OBS_LOG_FORMAT=json`` is set.  In containerised
    deployments (Docker / Kubernetes) this produces one JSON object per line
    on stdout, which Promtail / Fluentd / any LGTM-compatible collector can
    ingest directly for Loki without a sidecar parser.

    Safe to call multiple times — idempotent.
    """
    if (os.getenv("OBS_LOG_FORMAT") or "").strip().lower() != "json":
        return
    formatter = StructuredJsonFormatter()
    for handler in logging.root.handlers:
        if hasattr(handler, "stream"):
            if not isinstance(handler.formatter, StructuredJsonFormatter):
                handler.setFormatter(formatter)


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
        index_path=log_dir
        / os.getenv("OBS_LLM_AUDIT_INDEX_FILE", "llm_io_index.jsonl"),
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
        enabled = (
            _is_truthy(os.getenv("DEBUG") or os.getenv("FATEFORGER_DEBUG"))
            or _debugger_attached()
        )
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
        index_path=log_dir
        / os.getenv("TIMEBOX_PATCHER_INDEX_FILE", "timebox_patcher_index.jsonl"),
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


def _coerce_autogen_events_mode(value: str | None) -> str:
    raw = (value or "summary").strip().lower()
    alias = {
        "0": "off",
        "false": "off",
        "none": "off",
        "1": "full",
        "true": "full",
        "on": "full",
    }
    mode = alias.get(raw, raw)
    if mode not in {"summary", "full", "off"}:
        raise ValueError("AUTOGEN_EVENTS_LOG must be one of: summary, full, off")
    return mode


def _coerce_autogen_events_output_target(value: str | None) -> str:
    target = (value or "stdout").strip().lower()
    if target not in {"stdout", "audit"}:
        raise ValueError("AUTOGEN_EVENTS_OUTPUT_TARGET must be one of: stdout, audit")
    return target


def _coerce_autogen_events_full_payload_mode(value: str | None) -> str:
    mode = (value or "sanitized").strip().lower()
    if mode not in {"sanitized", "raw"}:
        raise ValueError(
            "AUTOGEN_EVENTS_FULL_PAYLOAD_MODE must be one of: sanitized, raw"
        )
    return mode


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


def _extract_context_from_agent_id(
    agent_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Derive session/channel/thread context from standard agent id shapes."""
    raw = str(agent_id or "").strip()
    if not raw or "/" not in raw:
        return None, None, None
    _, _, key = raw.partition("/")
    if not key:
        return None, None, None
    channel, sep, thread = key.partition(":")
    if not sep or not channel or not thread:
        return key, None, None
    if _CHANNEL_ID_RE.match(channel):
        return f"{channel}:{thread}", channel, thread
    return key, None, None


def _extract_stage_from_agent_id(agent_id: str | None) -> str | None:
    base = str(agent_id or "").split("/", 1)[0].strip()
    if not base:
        return None
    match = _STAGE_AGENT_RE.match(base)
    if not match:
        return None
    return match.group("stage")


def _derive_call_label(
    *,
    call_label: str | None,
    stage: str | None,
    agent_id: str | None,
    event_type: str,
) -> str:
    if call_label and call_label.strip():
        return call_label.strip()
    if stage and stage.strip():
        return stage.strip()
    stage_from_agent = _extract_stage_from_agent_id(agent_id)
    if stage_from_agent:
        return stage_from_agent
    base_agent = str(agent_id or "").split("/", 1)[0].strip()
    if base_agent:
        return base_agent
    return event_type


def _extract_model_from_response(raw_response: Any) -> str | None:
    if isinstance(raw_response, dict):
        model = raw_response.get("model")
        return str(model).strip() if isinstance(model, str) and model.strip() else None
    model = getattr(raw_response, "model", None)
    return str(model).strip() if isinstance(model, str) and model.strip() else None


def _extract_usage_tokens_from_response(
    raw_response: Any,
) -> tuple[int | None, int | None]:
    usage = None
    if isinstance(raw_response, dict):
        usage = raw_response.get("usage")
    else:
        usage = getattr(raw_response, "usage", None)
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
    else:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
    prompt_int = prompt if isinstance(prompt, int) and prompt >= 0 else None
    completion_int = (
        completion if isinstance(completion, int) and completion >= 0 else None
    )
    return prompt_int, completion_int


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
        return [
            _sanitize_for_audit(item, max_chars=max_chars, key=key) for item in value
        ]
    if isinstance(value, tuple):
        return [
            _sanitize_for_audit(item, max_chars=max_chars, key=key) for item in value
        ]
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


def _record_observability_event(payload: dict[str, Any], *, record_level: int) -> None:
    """Record Prometheus metrics and LLM audit events for an AutoGen event payload.

    Dispatches on the typed Pydantic model returned by :func:`parse_autogen_event`;
    unknown or invalid payloads are silently ignored.
    """
    from fateforger.core.autogen_event_models import (  # noqa: PLC0415
        ExceptionPayload,
        LLMEventPayload,
        ToolCallPayload,
        parse_autogen_event,
    )

    _ensure_metrics_initialized()
    event = parse_autogen_event(payload)
    if event is None:
        return

    derived_session_key, derived_channel_id, derived_thread_ts = (
        _extract_context_from_agent_id(event.agent_id)
    )
    session_key = event.session_key or derived_session_key
    thread_ts = event.thread_ts or derived_thread_ts
    channel_id = event.channel_id or derived_channel_id
    agent = _bounded_label(event.agent_id, fallback="unknown")
    call_label = _bounded_label(
        _derive_call_label(
            call_label=event.call_label,
            stage=event.stage,
            agent_id=event.agent_id,
            event_type=event.type,
        ),
        fallback="unknown",
    )

    if isinstance(event, LLMEventPayload):
        raw_response = payload.get("response")
        model_candidate = event.response_model
        if model_candidate == "unknown":
            model_candidate = (
                _extract_model_from_response(raw_response)
                or _extract_model_from_response(event.response)
                or "unknown"
            )
        model = _bounded_label(model_candidate, fallback="unknown")
        status = (
            "error"
            if (record_level >= logging.ERROR or event.response_error)
            else event.response_status
        )
        if _METRIC_LLM_CALLS is not None:
            _METRIC_LLM_CALLS.labels(
                agent=agent,
                model=model,
                status=status,
                call_label=call_label,
            ).inc()
        prompt_tokens = event.prompt_tokens or event.prompt_tokens_from_response()
        completion_tokens = (
            event.completion_tokens or event.completion_tokens_from_response()
        )
        extra_prompt, extra_completion = _extract_usage_tokens_from_response(
            raw_response
        )
        if prompt_tokens is None:
            prompt_tokens = extra_prompt
        if completion_tokens is None:
            completion_tokens = extra_completion
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
                "event_type": event.type,
                "agent": event.agent_id,
                "model": model,
                "status": status,
                "call_label": call_label,
                "stage": event.stage,
                "session_key": session_key,
                "thread_ts": thread_ts,
                "channel_id": channel_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "request_excerpt": event.model_extra.get("messages"),
                "response_excerpt": event.response,
                "error": event.response_error,
            }
        )
        return

    if isinstance(event, ToolCallPayload):
        if _METRIC_TOOL_CALLS is not None:
            _METRIC_TOOL_CALLS.labels(
                agent=agent,
                tool=_bounded_label(event.tool_name, fallback="unknown"),
                status="ok" if record_level < logging.ERROR else "error",
            ).inc()
        return

    if isinstance(event, ExceptionPayload):
        exc_session_key, exc_channel_id, exc_thread_ts = _extract_context_from_agent_id(
            event.component
        )
        if _METRIC_ERRORS is not None:
            _METRIC_ERRORS.labels(
                component=_bounded_label(event.component, fallback="unknown"),
                error_type=_bounded_label(
                    event.error_type or event.type, fallback="error"
                ),
            ).inc()
        _emit_llm_audit_event(
            {
                "event_type": event.type,
                "component": event.component,
                "error": event.exception,
                "session_key": event.session_key or exc_session_key,
                "thread_ts": event.thread_ts or exc_thread_ts,
                "channel_id": event.channel_id or exc_channel_id,
                "stage": event.stage,
            }
        )


class _AutogenEventsFilter(logging.Filter):
    def __init__(
        self,
        *,
        mode: str,
        max_chars: int,
        max_tools: int,
        output_target: str = "stdout",
        full_payload_mode: str = "sanitized",
    ) -> None:
        super().__init__(name="autogen_core.events")
        self._mode = _coerce_autogen_events_mode(mode)
        self._max_chars = max(200, max_chars)
        self._max_tools = max(1, max_tools)
        self._output_target = _coerce_autogen_events_output_target(output_target)
        self._full_payload_mode = _coerce_autogen_events_full_payload_mode(
            full_payload_mode
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "autogen_core.events":
            return True

        mode = self._mode
        raw_obj = record.msg
        msg = _safe_get_record_message(record)
        payload = _extract_event_payload(raw_obj, msg)
        if payload is not None:
            _record_observability_event(payload, record_level=record.levelno)
        if self._output_target == "audit":
            return False
        if mode == "off":
            return False
        record.msg = msg
        record.args = ()
        if mode == "full":
            if payload is not None:
                record.msg = _serialize_autogen_payload_for_stdout(
                    payload=payload,
                    full_payload_mode=self._full_payload_mode,
                    max_chars=self._max_chars,
                )
            return True

        # "summary" (default)
        if payload is not None:
            summarized = _summarize_autogen_event_payload(
                payload, max_chars=self._max_chars, max_tools=self._max_tools
            )
        else:
            summarized = _summarize_autogen_event_message(
                msg, max_chars=self._max_chars, max_tools=self._max_tools
            )
        record.msg = summarized
        record.args = ()
        return True


def _serialize_autogen_payload_for_stdout(
    *,
    payload: dict[str, Any],
    full_payload_mode: str,
    max_chars: int,
) -> str:
    if full_payload_mode == "raw":
        return _truncate(
            json.dumps(payload, ensure_ascii=False, default=str), max_chars=max_chars
        )
    return _truncate(
        json.dumps(
            _sanitize_for_audit(payload, max_chars=max_chars),
            ensure_ascii=False,
            default=str,
        ),
        max_chars=max_chars,
    )


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
    """Return a compact human-readable summary of an AutoGen event message string.

    Dispatches on the typed Pydantic model returned by :func:`parse_autogen_event`
    so there are no brittle substring probes or ``dict.get``-chains for type detection.
    """
    if not msg:
        return msg

    # Quick return: short message without structured JSON content.
    if len(msg) <= max_chars and msg[:1] != "{":
        return msg

    payload: dict[str, Any] | None = None
    if msg[:1] == "{" and msg[-1:] == "}":
        try:
            payload = json.loads(msg)
        except Exception:
            payload = None

    if not isinstance(payload, dict):
        return _truncate(msg, max_chars=max_chars)

    return _summarize_autogen_event_payload(
        payload, max_chars=max_chars, max_tools=max_tools
    )


def _summarize_autogen_event_payload(
    payload: dict[str, Any], *, max_chars: int, max_tools: int
) -> str:
    from fateforger.core.autogen_event_models import (  # noqa: PLC0415
        LLMEventPayload,
        MessageEventPayload,
        parse_autogen_event,
    )

    event = parse_autogen_event(payload)
    if isinstance(event, MessageEventPayload):
        return _summarize_autogen_message_event(event, max_chars=max_chars)
    if not isinstance(event, LLMEventPayload):
        return _truncate(
            json.dumps(
                _sanitize_for_audit(payload, max_chars=max_chars),
                ensure_ascii=False,
                default=str,
            ),
            max_chars=max_chars,
        )

    summary_parts: list[str] = [f"autogen type={event.type}"]
    if event.agent_id:
        summary_parts.append(f"agent_id={event.agent_id}")
    model_name = event.response_model
    if model_name and model_name != "unknown":
        summary_parts.append(f"model={model_name}")

    resp = event.response_obj
    finish_reason = resp.finish_reason if resp else None
    tool_calls = resp.tool_call_names if resp else []

    tool_names: list[str] = []
    for t in event.model_extra.get("tools") or []:
        if isinstance(t, dict):
            fn = t.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name:
                tool_names.append(name)

    if finish_reason:
        summary_parts.append(f"finish={finish_reason}")
    if tool_calls:
        summary_parts.append(f"tool_calls={_format_list(tool_calls, max_items=5)}")
    if tool_names:
        summary_parts.append(
            f"tools={len(tool_names)} {_format_list(tool_names, max_items=max_tools)}"
        )

    usage = resp.usage if resp else None
    if usage and any(
        v is not None
        for v in (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    ):
        summary_parts.append(
            f"tokens={usage.prompt_tokens}/{usage.completion_tokens}/{usage.total_tokens}"
        )

    return _truncate(" ".join(summary_parts), max_chars=max_chars)


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


def _summarize_autogen_message_event(
    event: "MessageEventPayload", *, max_chars: int
) -> str:
    """Render a compact summary of a Message routing event.

    Accepts a typed :class:`MessageEventPayload` so we avoid re-parsing the JSON
    payload string and the ``isinstance(payload_obj, dict)`` chain.
    """
    from fateforger.core.autogen_event_models import (  # noqa: PLC0415
        MessageEventPayload,
    )

    sender = event.sender
    receiver = event.receiver
    kind = event.kind
    stage = event.delivery_stage

    # parsed_payload already handles json.loads + type-check for us.
    payload_obj = event.parsed_payload

    payload_type = None
    msg_id = None
    source = None
    content = None
    usage = None
    if payload_obj is not None:
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
        from fateforger.core.autogen_event_models import LLMUsage  # noqa: PLC0415

        u = LLMUsage.model_validate(usage)
        if u.prompt_tokens is not None or u.completion_tokens is not None:
            parts.append(f"tokens={u.prompt_tokens}/{u.completion_tokens}")

    if isinstance(content, str) and content:
        content_one_line = " ".join(content.splitlines())
        parts.append(f"content={_truncate(content_one_line, max_chars=220)}")

    return _truncate(" ".join(parts), max_chars=max_chars)

    return _truncate(" ".join(parts), max_chars=max_chars)

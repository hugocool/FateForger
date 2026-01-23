import json
import logging
import os
import re
from typing import Any


def configure_logging(*, default_level: str | int = "INFO") -> None:
    """
    Configure application logging with sane defaults.

    Key behavior: reduce extremely large `autogen_core.events` INFO logs by default.
    """

    logging.basicConfig(level=_coerce_level(os.getenv("LOG_LEVEL", default_level)))

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


def _coerce_level(value: str | int) -> int:
    if isinstance(value, int):
        return value
    name = (value or "").strip().upper()
    return getattr(logging, name, logging.INFO)


def _coerce_int(value: str | None, *, default: int) -> int:
    try:
        if value is None:
            return default
        return int(str(value).strip())
    except Exception:
        return default


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
        if mode in ("off", "false", "0", "none"):
            return False
        if mode in ("full", "on", "true", "1"):
            return True

        # "summary" (default)
        msg = record.getMessage()
        summarized = _summarize_autogen_event_message(
            msg, max_chars=self._max_chars, max_tools=self._max_tools
        )
        record.msg = summarized
        record.args = ()
        return True


class _AutogenCoreFilter(logging.Filter):
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

        msg = record.getMessage()
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


def _summarize_autogen_event_message(
    msg: str, *, max_chars: int, max_tools: int
) -> str:
    if not msg:
        return msg

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

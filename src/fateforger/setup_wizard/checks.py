from __future__ import annotations

import asyncio
import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
from autogen_core import CancellationToken
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    name: str
    details: dict[str, Any]
    error: str | None = None


def _safe_error(e: BaseException) -> str:
    # Avoid leaking secrets from exception repr; keep it minimal.
    return f"{type(e).__name__}: {e}"


def _json_ok(path: Path) -> tuple[bool, str | None]:
    if not path.exists():
        return False, "file-missing"
    # TODO(refactor): Validate JSON files with a Pydantic schema.
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True, None
    except Exception as e:
        return False, _safe_error(e)


def _looks_like_not_running(error: str | None) -> bool:
    # TODO(refactor,typed-errors): Replace substring-based connectivity detection
    # with typed transport error classes / status categories.
    if not error:
        return False
    lowered = error.lower()
    return any(
        needle in lowered
        for needle in [
            "connecterror",
            "connection refused",
            "name or service not known",
            "nodename nor servname provided",
            "timeout",
        ]
    )


async def _probe_mcp_tools(
    *,
    name: str,
    url: str,
    headers: dict[str, Any] | None = None,
    timeout_s: float = 5.0,
    try_mcp_suffix: bool = True,
) -> tuple[list[Any], str, Optional[str]]:
    last_err: Optional[str] = None
    attempted_urls: list[str] = [url]
    if try_mcp_suffix and not url.rstrip("/").endswith("/mcp"):
        attempted_urls.append(url.rstrip("/") + "/mcp")

    for attempt_url in attempted_urls:
        try:
            params = StreamableHttpServerParams(
                url=attempt_url,
                headers=headers,
                timeout=timeout_s,
                sse_read_timeout=timeout_s,
            )
            tools = await mcp_server_tools(params)
            if tools:
                return tools, attempt_url, None
            last_err = f"No tools returned from {attempt_url}"
        except Exception as e:
            last_err = _safe_error(e)

    return [], attempted_urls[-1], last_err


async def check_calendar_mcp() -> CheckResult:
    """Verify Calendar MCP is reachable and exposes tools."""
    port = os.getenv("PORT", "3000")
    # Inside docker network we can reach by service name.
    url = (
        os.getenv("WIZARD_CALENDAR_MCP_URL")
        or os.getenv("MCP_CALENDAR_SERVER_URL")
        or f"http://calendar-mcp:{port}"
    )

    try:
        tools, resolved, err = await _probe_mcp_tools(
            name="calendar-mcp", url=url, timeout_s=3.0, try_mcp_suffix=False
        )
        tool_names = [getattr(t, "name", None) for t in tools]
        ok = bool(tools) and err is None
        return CheckResult(
            ok=ok,
            name="calendar-mcp",
            details={
                "url": resolved,
                "tool_count": len(tools),
                "tool_names_sample": [n for n in tool_names if n][:8],
            },
            error=err,
        )
    except Exception as e:
        return CheckResult(
            ok=False,
            name="calendar-mcp",
            details={"url": url},
            error=_safe_error(e),
        )


async def check_notion_mcp() -> CheckResult:
    port = os.getenv("MCP_HTTP_PORT", "3001")
    url = os.getenv("WIZARD_NOTION_MCP_URL") or f"http://notion-mcp:{port}/mcp"
    auth = os.getenv("MCP_HTTP_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {auth}"} if auth else None

    tools, resolved, err = await _probe_mcp_tools(
        name="notion-mcp", url=url, headers=headers, timeout_s=2.0, try_mcp_suffix=True
    )
    tool_names = [getattr(t, "name", None) for t in tools]
    ok = bool(tools) and err is None

    # Optional: confirm the Notion API token looks plausible.
    notion_token = os.getenv("NOTION_TOKEN")
    notion_token_ok = bool(notion_token and notion_token.startswith("ntn_"))

    return CheckResult(
        ok=ok and notion_token_ok,
        name="notion-mcp",
        details={
            "url": resolved,
            "tool_count": len(tools),
            "tool_names_sample": [n for n in tool_names if n][:8],
            "notion_token_present": bool(notion_token),
            "notion_token_format_ok": notion_token_ok,
            "auth_token_present": bool(auth),
            "service_reachable": not _looks_like_not_running(err),
        },
        error=(
            err
            if err
            else (None if notion_token_ok else "NOTION_TOKEN missing/invalid")
        ),
    )


async def check_ticktick_mcp() -> CheckResult:
    url = os.getenv("WIZARD_TICKTICK_MCP_URL") or "http://ticktick-mcp:8000"

    tools, resolved, err = await _probe_mcp_tools(
        name="ticktick-mcp", url=url, timeout_s=2.0, try_mcp_suffix=True
    )

    # Try a cheap real call if possible to validate credentials (read-only).
    invocation: dict[str, Any] = {"attempted": False}
    try:
        get_projects = next(
            (t for t in tools if getattr(t, "name", "") == "get_projects"), None
        )
        if get_projects:
            invocation["attempted"] = True
            res = await get_projects.run_json({}, CancellationToken())
            invocation["ok"] = True
            # Avoid dumping full user data; just sizes.
            if isinstance(res, list):
                invocation["result_len"] = len(res)
            else:
                invocation["result_type"] = type(res).__name__
        else:
            invocation["ok"] = False
            invocation["reason"] = "get_projects tool not exposed"
    except Exception as e:
        invocation["ok"] = False
        invocation["error"] = _safe_error(e)

    tool_names = [getattr(t, "name", None) for t in tools]
    ok = (
        bool(tools)
        and err is None
        and (not invocation.get("attempted") or invocation.get("ok") is True)
    )

    return CheckResult(
        ok=ok,
        name="ticktick-mcp",
        details={
            "url": resolved,
            "tool_count": len(tools),
            "tool_names_sample": [n for n in tool_names if n][:8],
            "invocation": invocation,
            "service_reachable": not _looks_like_not_running(err),
        },
        error=err,
    )


async def check_toggl_mcp() -> CheckResult:
    url = os.getenv("WIZARD_TOGGL_MCP_URL") or "http://toggl-mcp:8000"

    tools, resolved, err = await _probe_mcp_tools(
        name="toggl-mcp", url=url, timeout_s=2.0, try_mcp_suffix=True
    )

    toggl_api_key_present = bool(
        (os.getenv("TOGGL_API_KEY") or "").strip()
        or (os.getenv("TOGGL_API_TOKEN") or "").strip()
        or (os.getenv("TOGGL_TOKEN") or "").strip()
    )

    invocation: dict[str, Any] = {"attempted": False}
    account: dict[str, Any] | None = None
    workspaces: list[dict[str, Any]] | None = None
    try:
        check_auth = next(
            (t for t in tools if getattr(t, "name", "") == "toggl_check_auth"), None
        )
        if check_auth:
            invocation["attempted"] = True
            res = await check_auth.run_json({}, CancellationToken())
            invocation["ok"] = True

            parsed: Any = res
            # TODO(refactor): Parse tool responses with a Pydantic model.
            if isinstance(res, dict) and isinstance(res.get("content"), list):
                text = next(
                    (
                        item.get("text")
                        for item in res["content"]
                        if isinstance(item, dict) and item.get("type") == "text"
                    ),
                    None,
                )
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
            elif isinstance(res, str):
                try:
                    parsed = json.loads(res)
                except Exception:
                    parsed = None

            if isinstance(parsed, dict):
                account = parsed.get("user")
                workspaces = parsed.get("workspaces")
            else:
                invocation["result_type"] = type(res).__name__
        else:
            invocation["ok"] = False
            invocation["reason"] = "toggl_check_auth tool not exposed"
    except Exception as e:
        invocation["ok"] = False
        invocation["error"] = _safe_error(e)

    tool_names = [getattr(t, "name", None) for t in tools]
    ok = (
        bool(tools)
        and err is None
        and (not invocation.get("attempted") or invocation.get("ok") is True)
    )

    return CheckResult(
        ok=ok,
        name="toggl-mcp",
        details={
            "url": resolved,
            "tool_count": len(tools),
            "tool_names_sample": [n for n in tool_names if n][:8],
            "invocation": invocation,
            "toggl_api_key_present": toggl_api_key_present,
            "account": account,
            "workspaces": workspaces,
            "service_reachable": not _looks_like_not_running(err),
        },
        error=err,
    )


async def check_slack() -> CheckResult:
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")

    if not bot_token or not bot_token.startswith("xoxb-"):
        return CheckResult(
            ok=False,
            name="slack",
            details={
                "bot_token_present": bool(bot_token),
                "app_token_present": bool(app_token),
            },
            error="SLACK_BOT_TOKEN missing/invalid",
        )

    client = AsyncWebClient(token=bot_token)
    details: dict[str, Any] = {
        "bot_token_present": True,
        "app_token_present": bool(app_token),
        "auth_test": None,
        "socket_mode": None,
    }

    try:
        auth = await client.auth_test()
        details["auth_test"] = {
            "ok": bool(auth.get("ok")),
            "team": auth.get("team"),
            "url": auth.get("url"),
            "user": auth.get("user"),
            "bot_id": auth.get("bot_id"),
        }
        if not auth.get("ok"):
            return CheckResult(ok=False, name="slack", details=details, error=str(auth))
    except Exception as e:
        return CheckResult(
            ok=False, name="slack", details=details, error=_safe_error(e)
        )

    # Permissions check: validate we can list channels (required for auto-provisioning).
    details["scopes"] = {"conversations_list": {"attempted": True}}
    try:
        await client.conversations_list(
            limit=1,
            types="public_channel,private_channel",
            exclude_archived=True,
        )
        details["scopes"]["conversations_list"]["ok"] = True
    except SlackApiError as e:
        resp = getattr(e, "response", None)
        body = resp.data if resp is not None else {}
        details["scopes"]["conversations_list"]["ok"] = False
        details["scopes"]["conversations_list"]["error"] = body.get("error") or _safe_error(e)
        details["scopes"]["conversations_list"]["needed"] = body.get("needed")
        details["scopes"]["conversations_list"]["provided"] = body.get("provided")
        return CheckResult(
            ok=False,
            name="slack",
            details=details,
            error=(
                "Slack token missing scopes required for workspace bootstrap. "
                "Add the needed scopes in the Slack app config and reinstall the app."
            ),
        )
    except Exception as e:
        details["scopes"]["conversations_list"]["ok"] = False
        details["scopes"]["conversations_list"]["error"] = _safe_error(e)
        return CheckResult(ok=False, name="slack", details=details, error=_safe_error(e))

    # Optional Socket Mode handshake (fast, but requires xapp- token)
    if app_token and app_token.startswith("xapp-"):
        try:
            socket_client = SocketModeClient(app_token=app_token, web_client=client)
            details["socket_mode"] = {"attempted": True}

            async def _connect_briefly():
                await socket_client.connect()
                await asyncio.sleep(1.0)
                await socket_client.disconnect()

            await asyncio.wait_for(_connect_briefly(), timeout=3.0)
            details["socket_mode"]["ok"] = True
        except Exception as e:
            details["socket_mode"]["ok"] = False
            details["socket_mode"]["error"] = _safe_error(e)
            return CheckResult(
                ok=False, name="slack", details=details, error=_safe_error(e)
            )

    return CheckResult(ok=True, name="slack", details=details, error=None)


async def run_all_checks() -> list[CheckResult]:
    results = await asyncio.gather(
        check_slack(),
        check_calendar_mcp(),
        check_notion_mcp(),
        check_ticktick_mcp(),
        check_toggl_mcp(),
        return_exceptions=False,
    )
    return list(results)


def config_snapshot() -> dict[str, Any]:
    """Non-secret snapshot for UI display."""
    env_keys = [
        "PORT",
        "TRANSPORT",
        "MCP_CALENDAR_SERVER_URL",
        "MCP_CALENDAR_SERVER_URL_DOCKER",
        "MCP_HTTP_PORT",
        "SLACK_SOCKET_MODE",
        "LLM_PROVIDER",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_HTTP_REFERER",
        "OPENROUTER_TITLE",
        "OPENROUTER_SEND_REASONING_EFFORT_HEADER",
        "LLM_MODEL_RECEPTIONIST",
        "LLM_MODEL_TIMEBOXING",
        "LLM_MODEL_TIMEBOXING_DRAFT",
        "LLM_MODEL_TIMEBOX_PATCHER",
        "LLM_MODEL_REVISOR",
        "LLM_MODEL_TASKS",
        "LLM_REASONING_EFFORT_TIMEBOXING",
        "LLM_REASONING_EFFORT_TIMEBOXING_DRAFT",
        "LLM_REASONING_EFFORT_REVISOR",
        "LLM_REASONING_EFFORT_TASKS",
        "LLM_REASONING_EFFORT_TIMEBOX_PATCHER",
        "ENVIRONMENT",
        "LOG_LEVEL",
    ]
    present_keys = [
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_SIGNING_SECRET",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "NOTION_TOKEN",
        "MCP_HTTP_AUTH_TOKEN",
        "TOGGL_API_KEY",
    ]

    return {
        "env": {k: os.getenv(k) for k in env_keys},
        "secrets_present": {k: bool(os.getenv(k)) for k in present_keys},
    }

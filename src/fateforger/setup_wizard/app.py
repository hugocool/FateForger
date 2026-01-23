from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .checks import config_snapshot, run_all_checks
from .envfile import read_env_file, update_env_file

# Global cache for health checks
_health_cache: dict[str, Any] = {"checks": None, "timestamp": None}


def _env_path() -> Path:
    return Path(os.getenv("WIZARD_ENV_PATH", "/config/.env"))


def _secrets_dir() -> Path:
    return Path(os.getenv("WIZARD_SECRETS_DIR", "/config/secrets"))


def _admin_token() -> str | None:
    return os.getenv("WIZARD_ADMIN_TOKEN")


def _session_secret() -> str:
    return os.getenv("WIZARD_SESSION_SECRET", "")


def _require_admin(request: Request) -> None:
    if request.session.get("wizard_ok") is True:
        return
    raise HTTPException(status_code=401, detail="Login required")


def _summarize_dir(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"present": False}
        if not path.is_dir():
            return {"present": True, "is_dir": False}

        files: list[str] = []
        count = 0
        for p in sorted(path.rglob("*")):
            if p.is_file():
                count += 1
                if len(files) < 12:
                    try:
                        files.append(str(p.relative_to(path)))
                    except Exception:
                        files.append(str(p))
        return {"present": True, "is_dir": True, "file_count": count, "sample": files}
    except Exception as e:
        return {"present": False, "error": f"{type(e).__name__}: {e}"}


def _status_from(check: Any | None, configured: bool) -> tuple[str, str, str]:
    """Returns (status_class, status_text, subtitle)."""
    if check is not None and getattr(check, "ok", False) is True:
        return "ok", "OK", "Working"

    if not configured:
        return "warn", "Not configured", "Needs setup"

    # Configured, but not healthy
    details = getattr(check, "details", {}) if check is not None else {}
    reachable = details.get("service_reachable")
    if reachable is False:
        return "bad", "Not running", "Enable/start service"

    return "bad", "Needs attention", "Check keys/logs"


async def _get_health_checks(
    force_refresh: bool = False,
) -> tuple[list[Any], str | None]:
    """Get health checks from cache or run fresh if needed.

    Returns:
        (checks, last_checked_iso) where last_checked_iso is None on first run
    """
    if force_refresh or _health_cache["checks"] is None:
        checks = await run_all_checks()
        _health_cache["checks"] = checks
        _health_cache["timestamp"] = datetime.utcnow()
        return checks, _health_cache["timestamp"].isoformat() + "Z"

    return _health_cache["checks"], (
        _health_cache["timestamp"].isoformat() + "Z"
        if _health_cache["timestamp"]
        else None
    )


def _build_integrations(
    *,
    env: dict[str, str],
    checks_by_name: dict[str, Any],
    gcal_present: bool,
) -> list[dict[str, Any]]:
    slack_configured = bool(env.get("SLACK_BOT_TOKEN"))
    notion_configured = bool(env.get("NOTION_TOKEN")) and bool(
        env.get("MCP_HTTP_AUTH_TOKEN")
    )
    ticktick_configured = bool(env.get("TICKTICK_CLIENT_ID")) and bool(
        env.get("TICKTICK_CLIENT_SECRET")
    )
    toggl_configured = bool(
        (env.get("TOGGL_API_KEY") or "").strip()
        or (env.get("TOGGL_API_TOKEN") or "").strip()
        or (env.get("TOGGL_TOKEN") or "").strip()
        or (env.get("WIZARD_TOGGL_MCP_URL") or "").strip()
    )

    items: list[dict[str, Any]] = []

    status_class, status_text, subtitle = _status_from(
        checks_by_name.get("slack"), slack_configured
    )
    items.append(
        {
            "id": "slack",
            "label": "Slack",
            "href": "/setup/slack",
            "status_class": status_class,
            "status_text": status_text,
            "subtitle": subtitle,
        }
    )

    status_class, status_text, subtitle = _status_from(
        checks_by_name.get("calendar-mcp"), gcal_present
    )
    items.append(
        {
            "id": "calendar-mcp",
            "label": "Google Calendar",
            "href": "/setup/google",
            "status_class": status_class,
            "status_text": status_text,
            "subtitle": subtitle,
        }
    )

    status_class, status_text, subtitle = _status_from(
        checks_by_name.get("notion-mcp"), notion_configured
    )
    items.append(
        {
            "id": "notion-mcp",
            "label": "Notion",
            "href": "/setup/notion",
            "status_class": status_class,
            "status_text": status_text,
            "subtitle": subtitle,
        }
    )

    status_class, status_text, subtitle = _status_from(
        checks_by_name.get("ticktick-mcp"), ticktick_configured
    )
    items.append(
        {
            "id": "ticktick-mcp",
            "label": "TickTick",
            "href": "/setup/ticktick",
            "status_class": status_class,
            "status_text": status_text,
            "subtitle": subtitle,
        }
    )

    status_class, status_text, subtitle = _status_from(
        checks_by_name.get("toggl-mcp"), toggl_configured
    )
    items.append(
        {
            "id": "toggl-mcp",
            "label": "Toggl",
            "href": "/setup/toggl",
            "status_class": status_class,
            "status_text": status_text,
            "subtitle": subtitle,
        }
    )

    return items


app = FastAPI(title="FateForger Setup & Diagnostics")

if _session_secret():
    app.add_middleware(SessionMiddleware, secret_key=_session_secret(), max_age=3600)
else:
    # Still add sessions, but require WIZARD_SESSION_SECRET for production security.
    app.add_middleware(SessionMiddleware, secret_key="dev-only", max_age=3600)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "admin_token_configured": bool(_admin_token()),
            "session_secret_configured": bool(_session_secret()),
        },
    )


@app.post("/login")
async def login(request: Request, token: str = Form("")):
    admin = _admin_token()
    if not admin:
        raise HTTPException(
            status_code=500,
            detail="WIZARD_ADMIN_TOKEN not set; wizard cannot be used safely.",
        )
    if secrets.compare_digest(token.strip(), admin):
        request.session["wizard_ok"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": "Invalid token",
            "admin_token_configured": True,
            "session_secret_configured": bool(_session_secret()),
        },
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.post("/health/refresh", dependencies=[Depends(_require_admin)])
async def refresh_health_checks(request: Request):
    """Force refresh all health checks and redirect back."""
    await _get_health_checks(force_refresh=True)
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("wizard_ok"):
        return RedirectResponse(url="/login", status_code=303)

    env_path = _env_path()
    secrets_dir = _secrets_dir()
    env = read_env_file(env_path)
    checks, last_checked = await _get_health_checks()
    checks_by_name = {c.name: c for c in checks}

    gcal_path = secrets_dir / "gcal-oauth.json"
    gcal_present = gcal_path.exists()

    calendar_tokens_summary = _summarize_dir(Path("/config/calendar-mcp-tokens"))
    ticktick_tokens_summary = _summarize_dir(Path("/config/ticktick-config"))

    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "env_path": str(env_path),
            "secrets_dir": str(secrets_dir),
            "env": env,
            "checks": checks,
            "checks_by_name": checks_by_name,
            "snapshot": config_snapshot(),
            "gcal_present": gcal_present,
            "calendar_tokens_summary": calendar_tokens_summary,
            "ticktick_tokens_summary": ticktick_tokens_summary,
            "integrations": integrations,
            "active_integration": "dashboard",
            "last_checked": last_checked,
        },
    )


@app.get(
    "/setup/slack", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_slack_page(request: Request):
    env_path = _env_path()
    env = read_env_file(env_path)
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_slack.html",
        {
            "env": env,
            "check": checks_by_name.get("slack"),
            "integrations": integrations,
            "active_integration": "slack",
        },
    )


@app.post(
    "/setup/slack", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_slack_submit(
    request: Request,
    slack_bot_token: str = Form(""),
    slack_app_token: str = Form(""),
    slack_signing_secret: str = Form(""),
):
    updates: dict[str, str] = {}

    def _maybe(key: str, value: str):
        v = (value or "").strip()
        if v:
            updates[key] = v

    _maybe("SLACK_BOT_TOKEN", slack_bot_token)
    _maybe("SLACK_APP_TOKEN", slack_app_token)
    _maybe("SLACK_SIGNING_SECRET", slack_signing_secret)
    update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_slack.html",
        {
            "env": env,
            "check": checks_by_name.get("slack"),
            "saved": True,
            "updates": sorted(updates.keys()),
            "integrations": integrations,
            "active_integration": "slack",
        },
    )


@app.get(
    "/setup/env", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_env_page(request: Request):
    env_path = _env_path()
    env = read_env_file(env_path)
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_env.html",
        {
            "env": env,
            "env_path": str(env_path),
            "integrations": integrations,
            "active_integration": "dashboard",
        },
    )


@app.post(
    "/setup/env", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_env_submit(
    request: Request,
    slack_bot_token: str = Form(""),
    slack_app_token: str = Form(""),
    slack_signing_secret: str = Form(""),
    openai_api_key: str = Form(""),
    openai_model: str = Form(""),
    openai_base_url: str = Form(""),
    llm_provider: str = Form(""),
    openrouter_api_key: str = Form(""),
    openrouter_base_url: str = Form(""),
    openrouter_http_referer: str = Form(""),
    openrouter_title: str = Form(""),
    openrouter_send_reasoning_effort_header: str = Form(""),
    openrouter_reasoning_effort_header: str = Form(""),
    llm_model_receptionist: str = Form(""),
    llm_model_admonisher: str = Form(""),
    llm_model_timeboxing: str = Form(""),
    llm_model_timeboxing_draft: str = Form(""),
    llm_model_timebox_patcher: str = Form(""),
    llm_model_planner: str = Form(""),
    llm_model_revisor: str = Form(""),
    llm_model_tasks: str = Form(""),
    llm_reasoning_effort_timeboxing: str = Form(""),
    llm_reasoning_effort_timeboxing_draft: str = Form(""),
    llm_reasoning_effort_revisor: str = Form(""),
    llm_reasoning_effort_tasks: str = Form(""),
    llm_reasoning_effort_timebox_patcher: str = Form(""),
    notion_token: str = Form(""),
    mcp_http_auth_token: str = Form(""),
):
    updates: dict[str, str] = {}

    def _maybe(key: str, value: str):
        v = (value or "").strip()
        if v:
            updates[key] = v

    _maybe("SLACK_BOT_TOKEN", slack_bot_token)
    _maybe("SLACK_APP_TOKEN", slack_app_token)
    _maybe("SLACK_SIGNING_SECRET", slack_signing_secret)
    _maybe("OPENAI_API_KEY", openai_api_key)
    _maybe("OPENAI_MODEL", openai_model)
    _maybe("OPENAI_BASE_URL", openai_base_url)
    _maybe("LLM_PROVIDER", llm_provider)
    _maybe("OPENROUTER_API_KEY", openrouter_api_key)
    _maybe("OPENROUTER_BASE_URL", openrouter_base_url)
    _maybe("OPENROUTER_HTTP_REFERER", openrouter_http_referer)
    _maybe("OPENROUTER_TITLE", openrouter_title)
    _maybe(
        "OPENROUTER_SEND_REASONING_EFFORT_HEADER",
        openrouter_send_reasoning_effort_header,
    )
    _maybe("OPENROUTER_REASONING_EFFORT_HEADER", openrouter_reasoning_effort_header)
    _maybe("LLM_MODEL_RECEPTIONIST", llm_model_receptionist)
    _maybe("LLM_MODEL_ADMONISHER", llm_model_admonisher)
    _maybe("LLM_MODEL_TIMEBOXING", llm_model_timeboxing)
    _maybe("LLM_MODEL_TIMEBOXING_DRAFT", llm_model_timeboxing_draft)
    _maybe("LLM_MODEL_TIMEBOX_PATCHER", llm_model_timebox_patcher)
    _maybe("LLM_MODEL_PLANNER", llm_model_planner)
    _maybe("LLM_MODEL_REVISOR", llm_model_revisor)
    _maybe("LLM_MODEL_TASKS", llm_model_tasks)
    _maybe("LLM_REASONING_EFFORT_TIMEBOXING", llm_reasoning_effort_timeboxing)
    _maybe("LLM_REASONING_EFFORT_TIMEBOXING_DRAFT", llm_reasoning_effort_timeboxing_draft)
    _maybe("LLM_REASONING_EFFORT_REVISOR", llm_reasoning_effort_revisor)
    _maybe("LLM_REASONING_EFFORT_TASKS", llm_reasoning_effort_tasks)
    _maybe("LLM_REASONING_EFFORT_TIMEBOX_PATCHER", llm_reasoning_effort_timebox_patcher)
    _maybe("NOTION_TOKEN", notion_token)
    _maybe("MCP_HTTP_AUTH_TOKEN", mcp_http_auth_token)

    result = update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_env.html",
        {
            "env": env,
            "env_path": str(_env_path()),
            "saved": True,
            "changed": result.changed,
            "updates": sorted(updates.keys()),
            "integrations": integrations,
            "active_integration": "dashboard",
        },
    )


@app.post(
    "/setup/notion/generate-auth-token",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_admin)],
)
async def notion_generate_token(request: Request):
    token = secrets.token_hex(32)
    update_env_file(_env_path(), {"MCP_HTTP_AUTH_TOKEN": token})
    return RedirectResponse(url="/setup/env", status_code=303)


@app.get(
    "/setup/google", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_google_page(request: Request):
    secrets_dir = _secrets_dir()
    gcal_path = secrets_dir / "gcal-oauth.json"
    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_path.exists()
    )
    return templates.TemplateResponse(
        request,
        "setup_google.html",
        {
            "gcal_present": gcal_path.exists(),
            "gcal_path": str(gcal_path),
            "integrations": integrations,
            "active_integration": "calendar-mcp",
        },
    )


@app.post(
    "/setup/google", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_google_submit(
    request: Request, gcal_oauth_json: UploadFile = File(...)
):
    secrets_dir = _secrets_dir()
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / "gcal-oauth.json"

    raw = await gcal_oauth_json.read()
    # Basic validation: must be valid JSON.
    try:
        import json

        # TODO(refactor): Validate OAuth JSON via a Pydantic schema.
        json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    target.write_bytes(raw)

    return RedirectResponse(url="/setup/google", status_code=303)


@app.get(
    "/setup/notion", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_notion_page(request: Request):
    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_notion.html",
        {
            "env": env,
            "check": checks_by_name.get("notion-mcp"),
            "integrations": integrations,
            "active_integration": "notion-mcp",
        },
    )


@app.post(
    "/setup/notion", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_notion_submit(
    request: Request,
    notion_token: str = Form(""),
    mcp_http_auth_token: str = Form(""),
):
    updates: dict[str, str] = {}
    if notion_token.strip():
        updates["NOTION_TOKEN"] = notion_token.strip()
    if mcp_http_auth_token.strip():
        updates["MCP_HTTP_AUTH_TOKEN"] = mcp_http_auth_token.strip()

    update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_notion.html",
        {
            "env": env,
            "check": checks_by_name.get("notion-mcp"),
            "saved": True,
            "updates": sorted(updates.keys()),
            "integrations": integrations,
            "active_integration": "notion-mcp",
        },
    )


@app.get(
    "/setup/ticktick",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_admin)],
)
async def setup_ticktick_page(request: Request):
    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_ticktick.html",
        {
            "env": env,
            "check": checks_by_name.get("ticktick-mcp"),
            "ticktick_tokens_summary": _summarize_dir(Path("/config/ticktick-config")),
            "integrations": integrations,
            "active_integration": "ticktick-mcp",
        },
    )


@app.post(
    "/setup/ticktick",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_admin)],
)
async def setup_ticktick_submit(
    request: Request,
    ticktick_client_id: str = Form(""),
    ticktick_client_secret: str = Form(""),
):
    updates: dict[str, str] = {}
    if ticktick_client_id.strip():
        updates["TICKTICK_CLIENT_ID"] = ticktick_client_id.strip()
    if ticktick_client_secret.strip():
        updates["TICKTICK_CLIENT_SECRET"] = ticktick_client_secret.strip()

    update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_ticktick.html",
        {
            "env": env,
            "saved": True,
            "updates": sorted(updates.keys()),
            "check": checks_by_name.get("ticktick-mcp"),
            "ticktick_tokens_summary": _summarize_dir(Path("/config/ticktick-config")),
            "integrations": integrations,
            "active_integration": "ticktick-mcp",
        },
    )


@app.get(
    "/setup/toggl", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_toggl_page(request: Request):
    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_toggl.html",
        {
            "env": env,
            "check": checks_by_name.get("toggl-mcp"),
            "integrations": integrations,
            "active_integration": "toggl-mcp",
        },
    )


@app.post(
    "/setup/toggl", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_toggl_submit(
    request: Request,
    toggl_api_key: str = Form(""),
    toggl_default_workspace_id: str = Form(""),
    toggl_cache_ttl: str = Form(""),
    toggl_cache_size: str = Form(""),
    wizard_toggl_mcp_url: str = Form(""),
):
    updates: dict[str, str] = {}
    if toggl_api_key.strip():
        updates["TOGGL_API_KEY"] = toggl_api_key.strip()
    if toggl_default_workspace_id.strip():
        updates["TOGGL_DEFAULT_WORKSPACE_ID"] = toggl_default_workspace_id.strip()
    if toggl_cache_ttl.strip():
        updates["TOGGL_CACHE_TTL"] = toggl_cache_ttl.strip()
    if toggl_cache_size.strip():
        updates["TOGGL_CACHE_SIZE"] = toggl_cache_size.strip()
    if wizard_toggl_mcp_url.strip():
        updates["WIZARD_TOGGL_MCP_URL"] = wizard_toggl_mcp_url.strip()

    update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    gcal_present = (_secrets_dir() / "gcal-oauth.json").exists()
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_present
    )
    return templates.TemplateResponse(
        request,
        "setup_toggl.html",
        {
            "env": env,
            "saved": True,
            "updates": sorted(updates.keys()),
            "check": checks_by_name.get("toggl-mcp"),
            "integrations": integrations,
            "active_integration": "toggl-mcp",
        },
    )


@app.get("/diagnostics.json", dependencies=[Depends(_require_admin)])
async def diagnostics_json():
    env_path = _env_path()
    secrets_dir = _secrets_dir()
    env = read_env_file(env_path)
    checks = await run_all_checks()
    return {
        "env_path": str(env_path),
        "secrets_dir": str(secrets_dir),
        "env_keys_present": sorted([k for k, v in env.items() if bool(v)]),
        "checks": [
            {"name": c.name, "ok": c.ok, "details": c.details, "error": c.error}
            for c in checks
        ],
    }


def main() -> None:
    import uvicorn

    port = int(os.getenv("WIZARD_PORT", "8080"))
    uvicorn.run(
        "fateforger.setup_wizard.app:app",
        host="0.0.0.0",
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()

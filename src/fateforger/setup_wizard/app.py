from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .checks import config_snapshot, run_all_checks
from .envfile import read_env_file, update_env_file


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("wizard_ok"):
        return RedirectResponse(url="/login", status_code=303)

    env_path = _env_path()
    secrets_dir = _secrets_dir()
    env = read_env_file(env_path)
    checks = await run_all_checks()

    gcal_path = secrets_dir / "gcal-oauth.json"
    gcal_present = gcal_path.exists()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "env_path": str(env_path),
            "secrets_dir": str(secrets_dir),
            "env": env,
            "checks": checks,
            "snapshot": config_snapshot(),
            "gcal_present": gcal_present,
        },
    )


@app.get("/setup/env", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def setup_env_page(request: Request):
    env_path = _env_path()
    env = read_env_file(env_path)
    return templates.TemplateResponse(
        request,
        "setup_env.html",
        {
            "env": env,
            "env_path": str(env_path),
        },
    )


@app.post("/setup/env", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def setup_env_submit(
    request: Request,
    slack_bot_token: str = Form(""),
    slack_app_token: str = Form(""),
    slack_signing_secret: str = Form(""),
    openai_api_key: str = Form(""),
    openai_model: str = Form(""),
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
    _maybe("NOTION_TOKEN", notion_token)
    _maybe("MCP_HTTP_AUTH_TOKEN", mcp_http_auth_token)

    result = update_env_file(_env_path(), updates)

    env = read_env_file(_env_path())
    return templates.TemplateResponse(
        request,
        "setup_env.html",
        {
            "env": env,
            "env_path": str(_env_path()),
            "saved": True,
            "changed": result.changed,
            "updates": sorted(updates.keys()),
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


@app.get("/setup/google", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def setup_google_page(request: Request):
    secrets_dir = _secrets_dir()
    gcal_path = secrets_dir / "gcal-oauth.json"
    return templates.TemplateResponse(
        request,
        "setup_google.html",
        {
            "gcal_present": gcal_path.exists(),
            "gcal_path": str(gcal_path),
        },
    )


@app.post("/setup/google", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def setup_google_submit(request: Request, gcal_oauth_json: UploadFile = File(...)):
    secrets_dir = _secrets_dir()
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / "gcal-oauth.json"

    raw = await gcal_oauth_json.read()
    # Basic validation: must be valid JSON.
    try:
        import json

        json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    target.write_bytes(raw)

    return RedirectResponse(url="/setup/google", status_code=303)


@app.get("/setup/ticktick", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
async def setup_ticktick_page(request: Request):
    env = read_env_file(_env_path())
    return templates.TemplateResponse(
        request,
        "setup_ticktick.html",
        {
            "env": env,
        },
    )


@app.post("/setup/ticktick", response_class=HTMLResponse, dependencies=[Depends(_require_admin)])
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
    return templates.TemplateResponse(
        request,
        "setup_ticktick.html",
        {
            "env": env,
            "saved": True,
            "updates": sorted(updates.keys()),
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

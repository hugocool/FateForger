#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def _require_env(keys: list[str]) -> list[str]:
    missing = []
    for key in keys:
        if not os.getenv(key):
            missing.append(key)
    return missing


def _check_url(url: str, *, timeout_s: float = 5.0) -> bool:
    try:
        r = httpx.get(url, timeout=timeout_s)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def _check_calendar_mcp(port: str) -> bool:
    url = f"http://localhost:{port}/healthz"
    headers = {"Accept": "text/event-stream"}
    try:
        with httpx.stream("GET", url, headers=headers, timeout=5.0) as r:
            return 200 <= r.status_code < 300
    except Exception:
        return False


def _compose_services() -> list[dict]:
    proc = _run(["docker", "compose", "ps", "--format", "json"])
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout)  # type: ignore[name-defined]
    except Exception:
        return []


def _service_running(services: list[dict], name: str) -> bool:
    for svc in services:
        if (svc.get("Service") or "") == name:
            return (svc.get("State") or "").lower() == "running"
    return False


def main() -> int:
    load_dotenv()

    missing = _require_env(
        [
            "SLACK_BOT_TOKEN",
            "SLACK_SIGNING_SECRET",
            "SLACK_APP_TOKEN",
            "OPENAI_API_KEY",
        ]
    )
    if missing:
        print("Missing required env vars in `.env`:")
        for key in missing:
            print(f"- {key}")
        return 2

    docker = _run(["docker", "--version"])
    if docker.returncode != 0:
        print("Docker not available; install Docker Desktop.")
        return 3

    ps = _run(["docker", "compose", "ps"])
    if ps.returncode != 0:
        print("`docker compose ps` failed:")
        print(ps.stderr.strip() or ps.stdout.strip())
        return 4

    services = _compose_services()
    if services:
        required = ["calendar-mcp"]
        missing_running = [svc for svc in required if not _service_running(services, svc)]
        if missing_running:
            print("Expected docker compose services not running:")
            for svc in missing_running:
                print(f"- {svc}")
            print("Tip: run `docker compose up -d --build` (or the VS Code task).")
            return 4

    port = os.getenv("PORT") or "3000"
    calendar_ok = _check_calendar_mcp(port)
    if not calendar_ok:
        print(f"Calendar MCP not healthy at http://localhost:{port}/healthz")
        print("Tip: ensure `calendar-mcp` is running and PORT matches the compose config.")
        return 5

    print("OK: required env vars present, docker compose reachable, calendar-mcp healthy.")
    print("Next: run the Slack bot via:")
    print("- VS Code launch `FateForger: Slack Bot (Socket Mode)` (local), or")
    print("- `docker compose up -d --build slack-bot` (container)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

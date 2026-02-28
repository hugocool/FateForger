#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import threading
import time
import json
from collections.abc import Sequence
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _default_user_token() -> str:
    """Resolve preferred user token env var for local dev runs."""
    return (
        os.getenv("SLACK_USER_TOKEN", "").strip()
        or os.getenv("SLACK_TEST_USER_TOKEN", "").strip()
    )


class DriverConfig(BaseModel):
    """Validated runtime configuration for the Slack user driver."""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_token: str
    channel: str
    thread_ts: str | None = None
    text: str | None = None
    poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=30.0)
    follow_bot_thread_seconds: float = Field(default=10.0, ge=0.0, le=60.0)

    @field_validator("user_token")
    @classmethod
    def _validate_user_token(cls, value: str) -> str:
        if not value.startswith("xoxp-"):
            raise ValueError(
                "Expected a Slack user token (xoxp-...). Set SLACK_USER_TOKEN/SLACK_TEST_USER_TOKEN or pass --user-token."
            )
        return value

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str) -> str:
        if len(value) < 9 or value[0] not in {"C", "G", "D"}:
            raise ValueError("Expected a Slack channel ID like C..., G..., or D...")
        return value

    @field_validator("thread_ts")
    @classmethod
    def _validate_thread_ts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split(".")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError("thread_ts must look like 1772194594.823669")
        return value


@dataclass
class RuntimeState:
    self_user_id: str
    thread_ts: str
    stop_event: threading.Event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an interactive timeboxing thread as a real Slack user token (xoxp). "
            "Use this to test the same path as manual Slack usage."
        )
    )
    parser.add_argument("--channel", required=True, help="Slack channel ID (e.g. C0AA6HC1RJL)")
    parser.add_argument(
        "--thread-ts",
        default=None,
        help="Existing thread_ts. If omitted, a new thread is created from --text.",
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Initial message when creating a new thread.",
    )
    parser.add_argument(
        "--user-token",
        default=_default_user_token(),
        help="Slack xoxp token (or set SLACK_USER_TOKEN / SLACK_TEST_USER_TOKEN).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Reply poll interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--follow-bot-thread-seconds",
        type=float,
        default=10.0,
        help=(
            "When starting a new thread, wait this long for a bot-created root "
            "message and then continue in that thread (default: 10.0)."
        ),
    )
    return parser


def _build_config(argv: Sequence[str] | None = None) -> DriverConfig:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = DriverConfig(
            user_token=args.user_token,
            channel=args.channel,
            thread_ts=args.thread_ts,
            text=args.text,
            poll_interval_seconds=args.poll_interval,
            follow_bot_thread_seconds=args.follow_bot_thread_seconds,
        )
    except ValidationError as exc:
        parser.error(str(exc))
        raise SystemExit(2) from exc
    if config.thread_ts is None and not (config.text and config.text.strip()):
        parser.error("When --thread-ts is omitted, --text is required to create a new thread.")
    return config


def _msg_text(msg: dict) -> str:
    text = (msg.get("text") or "").strip()
    if text:
        return text
    return "[non-text message]"


def _message_fingerprint(msg: dict) -> str:
    """Return a stable fingerprint so edited-in-place Slack messages are re-shown."""
    text = str(msg.get("text") or "")
    blocks = msg.get("blocks") or []
    try:
        blocks_text = json.dumps(blocks, sort_keys=True, ensure_ascii=False)
    except TypeError:
        blocks_text = str(blocks)
    return f"{text}\n{blocks_text}"


def _print_new_replies(
    client: WebClient, cfg: DriverConfig, state: RuntimeState, seen: dict[str, str]
) -> None:
    resp = client.conversations_replies(channel=cfg.channel, ts=state.thread_ts, inclusive=True, limit=200)
    messages = sorted(resp.get("messages", []), key=lambda m: m.get("ts", ""))
    for msg in messages:
        ts = str(msg.get("ts") or "")
        if not ts:
            continue
        fingerprint = _message_fingerprint(msg)
        if seen.get(ts) == fingerprint:
            continue
        seen[ts] = fingerprint
        from_self = msg.get("user") == state.self_user_id
        if from_self:
            continue
        author = msg.get("user") or msg.get("bot_id") or "unknown"
        print(f"\n[{author} @ {ts}] {_msg_text(msg)}")
        print("you> ", end="", flush=True)


def _poll_loop(client: WebClient, cfg: DriverConfig, state: RuntimeState, seen: dict[str, str]) -> None:
    while not state.stop_event.is_set():
        try:
            _print_new_replies(client, cfg, state, seen)
        except SlackApiError as exc:
            print(f"\n[poll-error] {exc.response.get('error', str(exc))}")
            print("you> ", end="", flush=True)
        state.stop_event.wait(cfg.poll_interval_seconds)


def _maybe_follow_bot_thread(
    *,
    client: WebClient,
    cfg: DriverConfig,
    self_user_id: str,
    seed_ts: str,
) -> str:
    deadline = time.time() + cfg.follow_bot_thread_seconds
    while time.time() < deadline:
        remaining = max(1, int(deadline - time.time()) + 1)
        resp = client.conversations_history(
            channel=cfg.channel,
            oldest=seed_ts,
            inclusive=True,
            limit=min(100, remaining * 5),
        )
        messages = sorted(resp.get("messages", []), key=lambda m: m.get("ts", ""))
        for msg in messages:
            msg_ts = str(msg.get("ts") or "")
            if not msg_ts or msg_ts == seed_ts:
                continue
            if str(msg.get("user") or "") == self_user_id:
                continue
            if msg.get("thread_ts"):
                continue
            return msg_ts
        time.sleep(min(cfg.poll_interval_seconds, 1.5))
    return seed_ts


def _start_or_attach_thread(
    client: WebClient, cfg: DriverConfig, self_user_id: str
) -> tuple[str, dict[str, str]]:
    if cfg.thread_ts:
        print(f"Attached to existing thread: {cfg.thread_ts}")
        return cfg.thread_ts, {}

    resp = client.chat_postMessage(channel=cfg.channel, text=cfg.text or "")
    thread_ts = str(resp["ts"])
    seen: dict[str, str] = {thread_ts: ""}
    print(f"Started thread: {thread_ts}")
    print(f"Seed text: {cfg.text}")
    if cfg.follow_bot_thread_seconds > 0:
        followed = _maybe_follow_bot_thread(
            client=client,
            cfg=cfg,
            self_user_id=self_user_id,
            seed_ts=thread_ts,
        )
        if followed != thread_ts:
            thread_ts = followed
            seen[thread_ts] = ""
            print(f"Following bot-created thread: {thread_ts}")
    return thread_ts, seen


def run_chat(cfg: DriverConfig) -> int:
    client = WebClient(token=cfg.user_token)
    auth = client.auth_test()
    self_user_id = str(auth["user_id"])

    thread_ts, seen = _start_or_attach_thread(client, cfg, self_user_id)
    state = RuntimeState(self_user_id=self_user_id, thread_ts=thread_ts, stop_event=threading.Event())

    poller = threading.Thread(target=_poll_loop, args=(client, cfg, state, seen), daemon=True)
    poller.start()

    print("Interactive mode. Commands: /exit, /show")
    try:
        while True:
            user_text = input("you> ").strip()
            if not user_text:
                continue
            if user_text == "/exit":
                break
            if user_text == "/show":
                _print_new_replies(client, cfg, state, seen)
                continue
            client.chat_postMessage(channel=cfg.channel, thread_ts=thread_ts, text=user_text)
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_event.set()
        poller.join(timeout=2.0)

    print("Stopped.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(".env")
    cfg = _build_config(argv)
    return run_chat(cfg)


if __name__ == "__main__":
    raise SystemExit(main())

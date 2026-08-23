#!/usr/bin/env python3
"""Drive one Slack turn against the running bot and read its reply back.

LIVE SLACK. This posts into a real workspace as a real person and cannot be
undone. It is deliberately not a pytest file: `pyproject.toml` sets
``testpaths = ["tests"]`` and ``python_files = "test_*.py"``, so nothing here
is collected by `pytest tests/`, and the hermetic suite stays hermetic. The
selection logic it depends on *is* unit-tested, offline, in
``tests/unit/test_slack_e2e_helper.py``.

How the turn is made:

  * The message is posted with ``SLACK_USER_TOKEN`` (xoxp), so it arrives from
    the human rather than from an app. The bot ignores its own messages, and a
    turn driven with the bot token would be a turn the bot never sees.
  * The reply is read with ``SLACK_BOT_TOKEN`` (xoxb), which holds
    ``channels:history`` and is a member of the channels that matter.
  * The bot's identity is discovered from ``auth.test``, never hardcoded. Its
    replies arrive in two shapes -- some carry ``user`` plus ``bot_id``, others
    are ``subtype: bot_message`` with a display name and no ``user`` at all,
    because the bot posts some messages under a custom username. Matching only
    the first shape silently misses half the conversation.

What it asserts, and what it refuses to assert:

  It asserts that a reply from that bot appeared in that thread inside the
  timeout. It prints the reply and stops there. Whether the reply is a *good*
  one is a judgement about what the words mean, and this repository sends
  those to a model rather than to a substring test (CLAUDE.md). Feed the
  printed text to one; do not grow a keyword check in here.

Choosing a channel:

  There is no default and there will not be one. The bot answers ordinary
  channel messages -- a stray run starts a real planning session in whatever
  channel it lands in. ``SLACK_E2E_CHANNEL_ID`` or ``--channel`` must be given
  explicitly. The bot token holds ``channels:manage``, so a dedicated channel
  can be made for this:

      curl -sS -X POST https://slack.com/api/conversations.create \\
        -H "Authorization: Bearer $SLACK_BOT_TOKEN" \\
        -d name=ff-e2e -d is_private=false

Usage:

    ./.venv/bin/python scripts/slack_e2e.py --dry-run --channel C0123ABC
    ./.venv/bin/python scripts/slack_e2e.py --channel C0123ABC --message "plan my saturday"

``--dry-run`` posts nothing. It proves both tokens authenticate, that the bot
is in the channel, and that the read path returns messages -- everything the
real run needs except the write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
SLACK_API = "https://slack.com/api"


class SlackError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotIdentity:
    """Who the bot is, as Slack reports it. Both fields are needed."""

    user_id: str
    bot_id: str | None


def call(method: str, token: str, payload: Mapping[str, object] | None = None, *, get: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=20.0) as client:
        if get:
            response = client.get(f"{SLACK_API}/{method}", headers=headers, params=payload or {})
        else:
            response = client.post(f"{SLACK_API}/{method}", headers=headers, data=payload or {})
    body = response.json()
    if not body.get("ok"):
        raise SlackError(f"{method} failed: {body.get('error')!r} ({body})")
    return body


# ---------------------------------------------------------------------------
# Reply selection -- pure, and unit-tested offline
# ---------------------------------------------------------------------------


def is_from_bot(message: Mapping[str, object], identity: BotIdentity) -> bool:
    """Did this message come from the bot under test?

    Two shapes, because the bot posts some messages under a custom display
    name. Those arrive as ``subtype: bot_message`` carrying ``bot_id`` and no
    ``user`` field at all; the rest carry ``user`` and ``bot_id`` together.
    Both are the same app, and a check on either field alone sees only half of
    what the bot said.
    """
    if identity.bot_id is not None and message.get("bot_id") == identity.bot_id:
        return True
    return message.get("user") == identity.user_id


def is_after(message: Mapping[str, object], after_ts: str) -> bool:
    """Strictly newer than ``after_ts``.

    Strict, because ``after_ts`` is the timestamp of the message this driver
    just posted: anything at or before it is history, and a thread that
    already contained an answer would otherwise return that old answer
    instantly and call the turn a success. Compared as numbers because they
    are numbers; a malformed one is "not newer" rather than an exception, so
    one odd message cannot abort a poll.
    """
    try:
        return float(str(message.get("ts", "0"))) > float(after_ts)
    except ValueError:
        return False


def first_bot_reply(
    messages: Iterable[Mapping[str, object]],
    identity: BotIdentity,
    after_ts: str,
) -> Mapping[str, object] | None:
    """Earliest message from the bot strictly newer than ``after_ts``.

    Ordered by timestamp rather than trusting the order Slack returned, and
    filtered by time so a thread's existing history cannot be mistaken for an
    answer to the message just posted.
    """
    candidates = [
        message
        for message in messages
        if is_from_bot(message, identity) and is_after(message, after_ts)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda m: float(str(m.get("ts", "0"))))


#: The placeholder this bot posts while a turn runs. Matching it is comparing
#: the bot's own emitted boilerplate, not reading anything a person wrote --
#: the same category as a tool name or an action id. It exists so the harness
#: can be told apart from the answer it is still producing.
PROCESSING_MARKER = "is thinking..."

#: The instant acknowledgement, posted before any work begins. Also the bot's
#: own emitted text, and also a sign of life rather than an answer.
ACK_MARKER = ":eyes:"


def is_placeholder(message: Mapping[str, object]) -> bool:
    """Whether this is the bot saying it started, rather than what it found.

    Both are worth timing and they are different questions. Time to the
    placeholder is time to any sign of life, which decides whether a wait
    feels like work or a hang. Time to the answer is the wait itself. A
    harness that conflates them reports a 7-second turn that took 46.
    """
    text = str(message.get("text") or "").strip()
    return text == ACK_MARKER or PROCESSING_MARKER in text


def reply_text(message: Mapping[str, object]) -> str:
    """The text of a reply, falling back to a JSON dump of its blocks.

    Block-only messages carry an empty ``text``; printing nothing would look
    like a bot that answered with silence.
    """
    text = str(message.get("text") or "")
    if text:
        return text
    blocks = message.get("blocks")
    return json.dumps(blocks, indent=2) if blocks else "<empty message>"


# ---------------------------------------------------------------------------
# Live operations
# ---------------------------------------------------------------------------


def load_tokens() -> tuple[str, str]:
    from dotenv import dotenv_values

    env = {**dotenv_values(REPO_ROOT / ".env"), **os.environ}
    user = env.get("SLACK_USER_TOKEN")
    bot = env.get("SLACK_BOT_TOKEN")
    missing = [name for name, value in (("SLACK_USER_TOKEN", user), ("SLACK_BOT_TOKEN", bot)) if not value]
    if missing:
        raise SlackError(f"missing {missing} in .env or environment")
    return str(user), str(bot)


def bot_identity(bot_token: str) -> BotIdentity:
    who = call("auth.test", bot_token)
    return BotIdentity(user_id=str(who["user_id"]), bot_id=who.get("bot_id"))


def await_reply(
    bot_token: str,
    channel: str,
    thread_ts: str,
    identity: BotIdentity,
    after_ts: str,
    *,
    timeout: float,
    interval: float = 3.0,
    sleep=time.sleep,
) -> Mapping[str, object] | None:
    deadline = time.monotonic() + timeout
    while True:
        body = call(
            "conversations.replies",
            bot_token,
            {"channel": channel, "ts": thread_ts, "limit": 50},
            get=True,
        )
        found = first_bot_reply(body.get("messages") or [], identity, after_ts)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        sleep(interval)


def await_redirected_reply(
    bot_token: str,
    redirect_channel: str,
    identity: BotIdentity,
    after_ts: str,
    *,
    timeout: float,
    interval: float = 1.5,
    sleep=time.sleep,
    on_root_seen=None,
) -> Mapping[str, object] | None:
    """Follow a session that was anchored somewhere other than where it began.

    A timeboxing request does not answer where it was asked. The handoff posts
    a session root into the dedicated timeboxing channel and the whole
    conversation continues in that thread -- deliberately, so the durable
    workspace is one place rather than wherever the request happened to
    originate.

    Polling only the origin thread therefore reports "no reply" while the bot
    is answering correctly a channel away. That happened, and it read as the
    system being broken when the fault was entirely in this helper.

    Finds the newest root in the redirect channel posted after our message,
    then waits for a bot reply inside it. Both halves matter: a root alone is
    an empty session, and a reply in an older thread belongs to someone else's
    run.
    """
    deadline = time.monotonic() + timeout
    while True:
        history = call(
            "conversations.history",
            bot_token,
            {"channel": redirect_channel, "limit": 10},
            get=True,
        )
        roots = [
            m
            for m in (history.get("messages") or [])
            if is_from_bot(m, identity) and is_after(m, after_ts)
        ]
        for root in roots:
            if on_root_seen is not None:
                on_root_seen()
            replies = call(
                "conversations.replies",
                bot_token,
                {"channel": redirect_channel, "ts": root.get("ts"), "limit": 50},
                get=True,
            )
            candidates = [
                m
                for m in (replies.get("messages") or [])
                if is_from_bot(m, identity) and is_after(m, str(root.get("ts")))
            ]
            for candidate in candidates:
                if is_placeholder(candidate):
                    # Sign of life, not an answer. Note it and keep waiting.
                    if on_root_seen is not None:
                        on_root_seen()
                    continue
                return candidate
        if time.monotonic() >= deadline:
            return None
        sleep(interval)


def await_any_reply(
    bot_token: str,
    *,
    origin_channel: str,
    origin_thread: str,
    redirect_channel: str | None,
    identity: BotIdentity,
    after_ts: str,
    timeout: float,
    interval: float = 0.4,
    sleep=time.sleep,
    on_sign_of_life=None,
) -> Mapping[str, object] | None:
    """Wait for an answer in either place, checking both on every pass.

    A turn can answer where it was asked or in the dedicated session channel,
    and which one is not knowable in advance. Checking them in sequence means
    the second place is only reached after the first has given up, so any
    timing taken through this function measures the give-up budget rather than
    the bot.

    A placeholder is a sign of life and not an answer: it is reported through
    `on_sign_of_life` and the wait continues.
    """
    deadline = time.monotonic() + timeout
    while True:
        found = first_bot_reply(
            (
                call(
                    "conversations.replies",
                    bot_token,
                    {"channel": origin_channel, "ts": origin_thread, "limit": 50},
                    get=True,
                ).get("messages")
                or []
            ),
            identity,
            after_ts,
        )
        if found is not None:
            if on_sign_of_life is not None:
                on_sign_of_life()
            # With a redirect channel configured, the origin thread only ever
            # carries signs of life -- the ack, the thinking state, the
            # "continuing in #channel" pointer. The answer is in the session by
            # definition, so treating anything here as the answer reports the
            # pointer's latency instead of the plan's.
            if redirect_channel is None and not is_placeholder(found):
                return found

        if redirect_channel:
            found = _redirected_answer(
                bot_token,
                redirect_channel,
                identity,
                after_ts,
                on_sign_of_life=on_sign_of_life,
            )
            if found is not None:
                return found

        if time.monotonic() >= deadline:
            return None
        sleep(interval)


def _redirected_answer(
    bot_token: str,
    redirect_channel: str,
    identity: BotIdentity,
    after_ts: str,
    *,
    on_sign_of_life=None,
) -> Mapping[str, object] | None:
    """One pass over any session this turn may have opened elsewhere."""
    history = call(
        "conversations.history",
        bot_token,
        {"channel": redirect_channel, "limit": 10},
        get=True,
    )
    for root in history.get("messages") or []:
        if not (is_from_bot(root, identity) and is_after(root, after_ts)):
            continue
        if on_sign_of_life is not None:
            on_sign_of_life()
        replies = call(
            "conversations.replies",
            bot_token,
            {"channel": redirect_channel, "ts": root.get("ts"), "limit": 50},
            get=True,
        )
        for candidate in replies.get("messages") or []:
            if not (
                is_from_bot(candidate, identity)
                and is_after(candidate, str(root.get("ts")))
            ):
                continue
            if is_placeholder(candidate):
                continue
            return candidate
    return None


def dry_run(channel: str) -> int:
    user_token, bot_token = load_tokens()
    human = call("auth.test", user_token)
    identity = bot_identity(bot_token)
    print(f"posting identity : {human['user']} ({human['user_id']})")
    print(f"bot identity     : user_id={identity.user_id} bot_id={identity.bot_id}")

    info = call("conversations.info", bot_token, {"channel": channel}, get=True)
    conv = info["channel"]
    print(f"channel          : #{conv.get('name')} ({channel}) is_member={conv.get('is_member')}")
    if not conv.get("is_member"):
        print("the bot is not in this channel; it will never see the message", file=sys.stderr)
        return 1

    history = call("conversations.history", bot_token, {"channel": channel, "limit": 20}, get=True)
    messages = history.get("messages") or []
    from_bot = [m for m in messages if is_from_bot(m, identity)]
    print(f"read path        : {len(messages)} recent messages, {len(from_bot)} from the bot")
    if not from_bot:
        print(
            "no bot messages in the last 20 -- readable in principle, unproven here",
            file=sys.stderr,
        )
        return 1
    newest = max(from_bot, key=lambda m: float(str(m.get("ts", "0"))))
    print(f"newest bot message: ts={newest.get('ts')} {reply_text(newest)[:120]!r}")
    print("dry run OK: both tokens work, the bot is present, and its messages read back")
    return 0


def live_turn(
    channel: str,
    message: str,
    thread_ts: str | None,
    timeout: float,
    redirect_channel: str | None = None,
) -> int:
    user_token, bot_token = load_tokens()
    identity = bot_identity(bot_token)

    payload: dict[str, object] = {"channel": channel, "text": message}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    posted = call("chat.postMessage", user_token, payload)
    sent_ts = str(posted["ts"])
    root = thread_ts or sent_ts
    print(f"posted ts={sent_ts} thread={root}")

    started = time.monotonic()
    first_visible: float | None = None

    def _note_first_visible() -> None:
        # Time to *anything* the user can see, which is a different number
        # from time to the answer and the one that decides whether a wait
        # feels like work or like a hang. A session root posted into the
        # timeboxing channel counts: it is the first evidence the request
        # landed.
        nonlocal first_visible
        if first_visible is None:
            first_visible = time.monotonic() - started

    # Both places, every pass. Watching the origin first and the redirect
    # channel only after it gave up floored "first visible" at whatever that
    # budget was -- 45s in the first version, 6s in the second, and both
    # numbers were this helper's polling strategy reported as the system's
    # latency. Twice was a mistake; a third would be a design.
    reply = await_any_reply(
        bot_token,
        origin_channel=channel,
        origin_thread=root,
        redirect_channel=redirect_channel,
        identity=identity,
        after_ts=sent_ts,
        timeout=timeout,
        on_sign_of_life=_note_first_visible,
    )
    if reply is None:
        print(f"no reply from the bot within {timeout:.0f}s", file=sys.stderr)
        print(
            "check `scripts/demo.py status` -- a bot serving stale code, or not "
            "running at all, fails exactly like this",
            file=sys.stderr,
        )
        return 1
    total = time.monotonic() - started
    print()
    print("timing")
    print(
        f"  first visible : {first_visible:5.1f}s"
        if first_visible is not None
        else "  first visible : never (the answer was the first thing shown)"
    )
    print(f"  full answer   : {total:5.1f}s")
    if first_visible is not None and total - first_visible > 5.0:
        print(
            f"  the user waited {total - first_visible:.1f}s after the first sign of life"
        )
    print()
    print(f"reply ts={reply.get('ts')}")
    print("---")
    print(reply_text(reply))
    print("---")
    print("A reply arrived. Whether it is a *good* reply is a judgement about")
    print("meaning: send the text above to a model, not to a substring test.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slack_e2e",
        description="Drive one live Slack turn against the running bot.",
    )
    parser.add_argument(
        "--channel",
        default=os.environ.get("SLACK_E2E_CHANNEL_ID"),
        help="channel id to post in (or SLACK_E2E_CHANNEL_ID). No default: the bot "
             "answers ordinary channel messages, so a wrong id starts a real session.",
    )
    parser.add_argument("--message", default="ping from scripts/slack_e2e.py")
    parser.add_argument("--thread-ts", default=None, help="reply into this thread instead of starting one")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--redirect-channel",
        default=os.environ.get("SLACK_E2E_REDIRECT_CHANNEL_ID"),
        help=(
            "channel a session may be anchored in instead of the origin "
            "thread; timeboxing answers in its own channel"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="prove auth and the read path; post nothing")
    args = parser.parse_args(argv)

    if not args.channel:
        parser.error(
            "no channel given. Pass --channel or set SLACK_E2E_CHANNEL_ID. "
            "Use a channel created for this; posting into a shared one starts a real session."
        )
    try:
        if args.dry_run:
            return dry_run(args.channel)
        return live_turn(
            args.channel,
            args.message,
            args.thread_ts,
            args.timeout,
            args.redirect_channel,
        )
    except SlackError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

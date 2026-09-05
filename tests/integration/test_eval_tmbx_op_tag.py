"""Eval (#171): does a model's FIRST patch of a session carry the ``op`` tag?

Unit tests pin the wire schema and the preamble sentence. This is the other
half CLAUDE.md requires: the same question put to a real model, resampled,
asserted on the rate. It reproduces the harness's view of tmbx in-process --
the bridged tool descriptions and input schemas from ``build_server``, and
the two resources the profile inlines into the system prompt -- and asks the
model to lay out an empty Monday.

Measured 2026-09-05 on google/gemini-3.6-flash, the model the joint run
(#149) was on when the bug was found:

    baseline (default, not required; no sentence)   8 of 20 tagged
    schema: op required, no default                 8 of 10
    schema + one preamble sentence                 28 of 30

The threshold below sits between the two distributions. A green run proves
the prompt still separates them; it does not prove a particular sample.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

import httpx
import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.core.ops import Patch
from tmbx.journal.store import JournalStore, init_journal
from tmbx.server import build_server
from tmbx.service import PlanService

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

MODEL = "google/gemini-3.6-flash"
SAMPLES = 8
MIN_TAGGED = 6  # baseline lands ~3 of 8; the fix lands 7-8 of 8

PERSONA = (
    "You build one timebox patch and return what happened. Nothing else.\n\n"
    "You have exactly two tools. `mcp__tmbx__plan_read` gives you the snapshot "
    "for the day; `mcp__tmbx__plan_apply` patches it. Call plan_read first and "
    "pass its snapshot back verbatim. Then apply one patch. The constraints you "
    "must satisfy are in the task you were given: treat them as complete."
)

TASK = """\
Build the patch for Monday 2026-09-08 (calendar primary). The day is empty.

Constraints (complete; do not invent others):
- MUST: morning ritual 07:00 for one hour (standing rule).
- MUST: deep work on the C2F pipeline 09:30-11:00 (standing rule).
- MUST: gym at 18:00 for one hour (standing rule).
- SHOULD: oats about two hours before the gym.
- Commute to the office takes 30 minutes and he wants to be in by 09:30.
- Lunch 30 minutes around noon; finances 45 minutes of shallow work in the
  afternoon; one more 90-minute deep-work block after lunch.

Read the day, then apply one patch that lays the whole day out."""


async def _harness_view(tmp_path) -> tuple[str, list[dict], dict]:
    """(system prompt, tool definitions, plan_read payload) as the harness sees them."""
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    service = PlanService(FakeCalendar({"primary": []}), store, mint_uid=lambda: "u-1")
    server = build_server(service)

    tools = []
    for tool in await server.list_tools():
        if tool.name in ("plan_read", "plan_apply"):
            tools.append({
                "type": "function",
                "function": {
                    "name": f"mcp__tmbx__{tool.name}",
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            })
    assert len(tools) == 2

    resources = {}
    for uri in ("tmbx://schema/ops", "tmbx://policy/planning"):
        contents = await server.read_resource(uri)
        resources[uri] = "".join(c.content for c in contents)
    system = (
        PERSONA
        + "\n\n# tmbx://schema/ops\n" + resources["tmbx://schema/ops"]
        + "\n\n# tmbx://policy/planning\n" + resources["tmbx://policy/planning"]
    )

    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": "2026-09-08"})
    payload = json.loads("".join(getattr(c, "text", "") for c in read))
    assert payload["ok"], payload
    return system, tools, payload


def _tagged(patch: object) -> bool:
    """True when every op carries ``op`` -- the thing the discriminator needs."""
    ops = patch.get("ops") if isinstance(patch, dict) else None
    if not isinstance(ops, list) or not ops:
        return False
    if not all(isinstance(op, dict) and "op" in op for op in ops):
        return False
    Patch.model_validate(patch)  # the tag alone is not the bar; the whole shape must parse
    return True


async def _first_patch(client: httpx.AsyncClient, system: str, tools: list[dict], read_payload: dict) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": TASK}]
    for _round in range(4):
        response = await client.post(
            f"{os.environ.get('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
            json={"model": MODEL, "messages": messages, "tools": tools},
        )
        response.raise_for_status()
        body = response.json()
        if "choices" not in body:
            raise RuntimeError(f"provider returned 200 without choices: {body.get('error', body)!r}")
        message = body["choices"][0]["message"]
        messages.append(message)
        for call in message.get("tool_calls") or []:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            if name.endswith("plan_read"):
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(read_payload)})
            elif name.endswith("plan_apply"):
                try:
                    return "tagged" if _tagged(args.get("patch")) else "untagged"
                except ValueError:
                    return "invalid_shape"
        if not message.get("tool_calls"):
            return "no_tool_call"
    return "no_apply"


async def test_the_first_patch_of_a_session_carries_the_op_tag(tmp_path):
    system, tools, read_payload = await _harness_view(tmp_path)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        outcomes = await asyncio.gather(
            *(_first_patch(client, system, tools, read_payload) for _ in range(SAMPLES))
        )
    counts = Counter(outcomes)
    assert counts["tagged"] >= MIN_TAGGED, f"{MODEL}: {dict(counts)} over {SAMPLES} draws"

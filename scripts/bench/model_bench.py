#!/usr/bin/env python3
"""Which model, and at which reasoning effort, makes the Slack agent feel fast.

WHAT IS MEASURED, AND WHY THIS METRIC

The turn is not one model call. It is a chain of them -- route, read, think,
answer -- each paying its own time-to-first-token before it can emit the tool
call that unblocks the next. So total wall clock is dominated by

    sum over steps of ( TTFT + output_tokens / throughput )

and the two terms do not trade off evenly. TTFT is paid on every step whether
the step emits three tokens or three hundred; throughput is paid only on the
steps that talk to a person. A model with half the throughput and half the
TTFT is *faster here* than the reverse, because most steps in this agent emit
a tool call and nothing else.

So TTFT is the headline and throughput is the second column, and they are
reported separately rather than blended into one score that would hide which
of the two a model is bad at.

Two things that are not latency but decide it:

  ROUND TRIPS. A model that issues three independent reads in one step costs
  one TTFT. A model that serialises them costs three, which at this prefix
  size buys more delay than any plausible throughput difference. Measured
  directly as tool calls per step on the task that has three independent
  reads available.

  PREFIX. Every step re-sends the system prompt and every tool schema. The
  live agent ships 26k characters and 34 tools for a job that needs 11, so
  the same prompt is run at both widths to price that separately from the
  model choice -- it is a config change, and it applies to whichever model
  wins.

SAMPLING. Every cell is sampled n times and reported as median plus spread.
A single draw measures the endpoint's luck (CLAUDE.md), and latency is far
noisier than the judgements that rule was written about.

Concurrency is per-model, never within one: models race each other so the
sweep finishes, but a model's own samples run one at a time so its numbers
are not measuring contention with itself.

    OPENROUTER_API_KEY=... python scripts/bench/model_bench.py --samples 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

#: The tools the agent actually needs. Everything else in the live mount is
#: reach it does not use, and `bash` is worse than unused: the system prompt
#: forbids shelling out and the model shells out anyway, because it is the
#: only way offered to learn today's date.
NEEDED_TOOLS = frozenset({
    "skill",
    "todo_write",
    "mcp__tmbx__plan_read",
    "mcp__tmbx__plan_apply",
    "mcp__tmbx__plan_commit",
    "mcp__tmbx__plan_undo",
    "mcp__tmbx__plan_history",
    "mcp__memory__memory_get_active_constraints",
    "mcp__memory__memory_get_suspended_constraints",
    "mcp__memory__memory_get_session_constraints",
    "mcp__memory__memory_observe",
})


@dataclass(frozen=True)
class Contender:
    label: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class Task:
    """One shape of response the agent has to produce."""

    key: str
    why: str
    messages: list[dict[str, Any]]
    #: None means "no tools this call" -- prose only.
    tools: str | None = "full"
    max_tokens: int = 700


@dataclass
class Sample:
    ttft: float | None = None
    total: float | None = None
    out_tokens: int = 0
    prompt_tokens: int = 0
    tool_calls: int = 0
    text: str = ""
    error: str | None = None
    reasoning_tokens: int = 0
    #: name + arguments per call. Without these the patch task cannot be
    #: graded at all: its entire output IS a tool call, so counting calls and
    #: discarding what they said measures that the model answered, never
    #: whether the answer was right.
    calls: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Cell:
    contender: str
    task: str
    samples: list[Sample] = field(default_factory=list)


def load_workload(scratch: Path) -> tuple[str, list[dict[str, Any]]]:
    system = (scratch / "system_prompt.txt").read_text(encoding="utf-8")
    tools = json.loads((scratch / "tools.json").read_text(encoding="utf-8"))
    return system, tools


def as_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Harness tool records -> OpenAI function-tool schema."""
    out = []
    for t in tools:
        name = t.get("name")
        if not name:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (t.get("description") or "")[:1024],
                "parameters": t.get("parameters") or t.get("inputSchema") or {"type": "object"},
            },
        })
    return out


def build_tasks(system: str) -> list[Task]:
    def convo(user: str, *, assistant_turns: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
        msgs.append({"role": "user", "content": user})
        msgs.extend(assistant_turns or [])
        return msgs

    plan_read_result = json.dumps({
        "ok": True,
        "snapshot": {"calendar_id": "primary", "day": "2026-08-24", "events": []},
    })
    constraints_result = json.dumps({
        "constraints": [
            {"label": "Sleep window", "necessity": "must", "detail": "23:00-07:00"},
            {"label": "Commute", "necessity": "must", "detail": "30 minutes each way"},
            {"label": "No morning meetings", "necessity": "must", "detail": "nothing before 13:00"},
            {"label": "Oats timing", "necessity": "should", "detail": "oats exactly 2h before gym"},
            {"label": "Gym session", "necessity": "should", "detail": "18:00-19:00"},
        ],
        "suspended_count": 2,
    })

    return [
        Task(
            key="route",
            why="First move: pick a skill and start reading. Tiny output, huge prefix — pure TTFT, and the 3-7s target lands here.",
            messages=convo("I want to do some timeboxing for tomorrow"),
            max_tokens=400,
        ),
        Task(
            key="parallel_reads",
            why="Three independent reads are available. Batching them costs one TTFT; serialising costs three.",
            messages=convo(
                "Plan tomorrow for me.",
                assistant_turns=[
                    {"role": "assistant", "content": "I'll load the timeboxing context first.",
                     "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "skill", "arguments": json.dumps({"skill": "timeboxing"})}}]},
                    {"role": "tool", "tool_call_id": "c1",
                     "content": "Loaded skill 'timeboxing'. Read the day with plan_read, then the "
                                "constraints with memory_get_active_constraints (pass day_type) and "
                                "memory_get_suspended_constraints. These are independent."},
                ],
            ),
            max_tokens=500,
        ),
        Task(
            key="stage_prose",
            why="The user-facing stage message. Throughput-dominated and the thing quality is judged on.",
            messages=convo(
                "I want to do some timeboxing for tomorrow",
                assistant_turns=[
                    {"role": "assistant", "content": "",
                     "tool_calls": [
                         {"id": "c1", "type": "function",
                          "function": {"name": "mcp__tmbx__plan_read",
                                       "arguments": json.dumps({"calendar_id": "primary", "day": "2026-08-24"})}},
                         {"id": "c2", "type": "function",
                          "function": {"name": "mcp__memory__memory_get_active_constraints",
                                       "arguments": json.dumps({"day": "2026-08-24", "day_type": "working"})}},
                     ]},
                    {"role": "tool", "tool_call_id": "c1", "content": plan_read_result},
                    {"role": "tool", "tool_call_id": "c2", "content": constraints_result},
                    {"role": "user", "content":
                        "Now give me the Stage 1 message: name the stage, list what you loaded, "
                        "say how many rules are suspended, and ask exactly one question."},
                ],
            ),
            tools=None,
            max_tokens=700,
        ),
        Task(
            key="progress_line",
            why="One intermittent update while work is in flight. Shortest possible output — isolates TTFT from everything else.",
            messages=[
                {"role": "system", "content":
                    "You narrate progress for a planning agent in Slack. One short line, "
                    "present tense, saying what you are doing right now. No preamble, no lists."},
                {"role": "user", "content":
                    "You have just called plan_read and two constraint reads for tomorrow. Narrate."},
            ],
            tools=None,
            max_tokens=80,
        ),
        Task(
            key="patch",
            why="A structured plan_apply patch. Correctness matters more than speed, and long structured output is where throughput bites.",
            messages=convo(
                "Block out tomorrow: deep work in the morning is out (no meetings before 13:00 "
                "is a MUST, and deep work is fine early), gym at 18:00, oats 2h before.",
                assistant_turns=[
                    {"role": "assistant", "content": "",
                     "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "mcp__tmbx__plan_read",
                                                  "arguments": json.dumps({"calendar_id": "primary", "day": "2026-08-24"})}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": plan_read_result},
                    {"role": "user", "content":
                        "Call plan_apply once with a patch that adds the blocks. Pass the snapshot back verbatim."},
                ],
            ),
            max_tokens=1200,
        ),
    ]


async def one_sample(
    client: httpx.AsyncClient,
    contender: Contender,
    task: Task,
    tools: list[dict[str, Any]] | None,
) -> Sample:
    body: dict[str, Any] = {
        "model": contender.model,
        "messages": task.messages,
        "max_tokens": task.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
    if contender.effort:
        body["reasoning"] = {"effort": contender.effort}

    sample = Sample()
    t0 = time.monotonic()
    try:
        async with client.stream("POST", f"{BASE_URL}/chat/completions", json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                sample.error = f"HTTP {resp.status_code}: {detail}"
                return sample
            seen_tool_ids: set[str] = set()
            building: dict[str, dict[str, str]] = {}
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                usage = event.get("usage")
                if usage:
                    sample.out_tokens = usage.get("completion_tokens", 0) or 0
                    sample.prompt_tokens = usage.get("prompt_tokens", 0) or 0
                    details = usage.get("completion_tokens_details") or {}
                    sample.reasoning_tokens = details.get("reasoning_tokens", 0) or 0
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    calls = delta.get("tool_calls")
                    if (content or calls) and sample.ttft is None:
                        # First *visible* token. Reasoning deltas deliberately
                        # do not count: the user cannot see them, so counting
                        # them would flatter exactly the setting under test.
                        sample.ttft = time.monotonic() - t0
                    if content:
                        sample.text += content
                    for call in calls or []:
                        # Key on `index`, not `id`. Only the FIRST delta of a
                        # call carries an id; the fragments that follow carry
                        # index alone. Preferring id therefore opened a second
                        # entry for every call — which doubled the tool-call
                        # count and split each call's name away from its own
                        # arguments. It read as parallelism and was an artifact.
                        index = call.get("index")
                        ident = str(index) if index is not None else call.get("id")
                        if ident is None:
                            continue
                        seen_tool_ids.add(ident)
                        slot = building.setdefault(ident, {"name": "", "arguments": ""})
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        # Arguments stream as fragments and must be joined in
                        # arrival order, never replaced.
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
            sample.tool_calls = len(seen_tool_ids)
            sample.calls = list(building.values())
    except Exception as exc:  # noqa: BLE001 - the error is the measurement
        sample.error = f"{type(exc).__name__}: {exc}"
    sample.total = time.monotonic() - t0
    return sample


async def run_contender(
    contender: Contender,
    tasks: list[Task],
    tool_sets: dict[str, list[dict[str, Any]]],
    samples: int,
    api_key: str,
    out: list[Cell],
) -> None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/hugocool/FateForger",
        "X-Title": "FateForger bench",
    }
    async with httpx.AsyncClient(headers=headers, timeout=180.0) as client:
        for task in tasks:
            cell = Cell(contender=contender.label, task=task.key)
            tools = tool_sets.get(task.tools) if task.tools else None
            for _ in range(samples):
                # Serial within a contender: concurrent self-samples would
                # measure queueing against itself rather than the endpoint.
                cell.samples.append(await one_sample(client, contender, task, tools))
            out.append(cell)
            ok = [s for s in cell.samples if s.error is None and s.ttft is not None]
            mark = f"{statistics.median(s.ttft for s in ok):.2f}s" if ok else "FAILED"
            print(f"  {contender.label:<38} {task.key:<16} ttft={mark}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--scratch", default=os.environ.get("BENCH_SCRATCH", "."))
    parser.add_argument("--out", default="bench_results.json")
    parser.add_argument("--narrow", action="store_true", help="also run the 11-tool prefix")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    scratch = Path(args.scratch)
    system, raw_tools = load_workload(scratch)
    full = as_openai_tools(raw_tools)
    narrow = [t for t in full if t["function"]["name"] in NEEDED_TOOLS]
    tool_sets = {"full": full, "narrow": narrow}
    tasks = build_tasks(system)

    contenders = [
        Contender("gemini-3.6-flash @low (LIVE)", "google/gemini-3.6-flash", "low"),
        Contender("gemini-3.6-flash @minimal", "google/gemini-3.6-flash", "minimal"),
        Contender("gemini-3.7-flash @minimal", "google/gemini-3.7-flash", "minimal"),
        Contender("gemini-3.5-flash-lite @minimal", "google/gemini-3.5-flash-lite", "minimal"),
        Contender("claude-haiku-4.5", "anthropic/claude-haiku-4.5", None),
        Contender("gpt-5.4-mini @minimal", "openai/gpt-5.4-mini", "minimal"),
    ]

    if args.narrow:
        # Same model, same prompts, fewer tool schemas. Prices the config
        # change separately from the model choice, since it applies to
        # whichever model wins.
        for task in tasks:
            if task.tools == "full":
                object.__setattr__(task, "tools", "narrow")
        print("PREFIX: narrow (11 tools)")

    print(f"system prompt: {len(system)} chars | tools: full={len(full)} narrow={len(narrow)}")
    print(f"{len(contenders)} contenders x {len(tasks)} tasks x {args.samples} samples\n")

    cells: list[Cell] = []

    async def sweep() -> None:
        await asyncio.gather(*(
            run_contender(c, tasks, tool_sets, args.samples, api_key, cells)
            for c in contenders
        ))

    asyncio.run(sweep())

    payload = {
        "system_chars": len(system),
        "tools_full": len(full),
        "tools_narrow": len(narrow),
        "samples": args.samples,
        "tasks": {t.key: t.why for t in tasks},
        "cells": [
            {
                "contender": c.contender,
                "task": c.task,
                "samples": [vars(s) for s in c.samples],
            }
            for c in cells
        ],
    }
    Path(args.out).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

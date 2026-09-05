#!/usr/bin/env python3
"""Grade the benchmark's outputs. Speed without quality picks the wrong model.

Every judgement here is about what generated text *means* -- did it name the
stage, did it ask one question or four, does this patch respect a MUST -- so it
goes to a model, never to a pattern (CLAUDE.md). Each sample is graded
independently and the result is reported as a RATE, because one passing draw
measures the endpoint's luck.

Escalated to a pro-tier judge on purpose: grading five criteria against a
structured patch is deliberation, not term typing, and it is off the write path
so its latency costs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import httpx

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
#: Overridable, because the grader is not the thing under test. The default is
#: deliberately NOT one of the contenders: a model grading its own output in a
#: comparison is a bias nobody can see in the table.
# The non-contender: a bench judged by one of its own contenders measures agreement,
# not quality. The pro pin is never a contender in the loop bench.
JUDGE = os.environ.get("BENCH_JUDGE") or os.environ.get("OPENROUTER_DEFAULT_MODEL_PRO") or "deepseek/deepseek-v4-pro-0813:nitro"

RUBRICS = {
    "stage_prose": """You are grading one message from a day-planning assistant to its user.

The assistant had just read an EMPTY calendar for 2026-08-24 and these rules:
Sleep 23:00-07:00 (must), Commute 30 min (must), No morning meetings before
13:00 (must), Oats exactly 2h before gym (should), Gym 18:00-19:00 (should).
The tool also reported suspended_count = 2. It was asked to give a Stage 1
message naming the stage, listing what it loaded, saying how many rules are
suspended, and asking exactly one question.

Grade these five, each true or false:
- names_stage: it identifies which stage it is in.
- lists_loaded: it reports the calendar state and the rules it read.
- reports_suspended: it states that 2 rules are suspended. Any phrasing counts;
  omitting the number entirely does not.
- one_question: it asks the user exactly ONE question. Two or more is false.
  Zero is also false.
- no_fabrication: it invents no rule, event or commitment that was not given.""",
    "patch": """You are grading one tool call from a day-planning assistant.

It had read an empty calendar for 2026-08-24 and was told: deep work in the
morning is fine, no meetings before 13:00 is a MUST, gym at 18:00, oats 2h
before gym. It was asked to call plan_apply ONCE with a patch adding the
blocks, passing the snapshot back verbatim.

Grade these five, each true or false:
- called_plan_apply: the call is to mcp__tmbx__plan_apply. Any other tool, or
  no call at all, is false.
- single_call: exactly one plan_apply call, not several.
- snapshot_passed: the arguments carry a snapshot object back.
- respects_must: nothing it schedules is a meeting before 13:00. Deep work,
  gym and meals before 13:00 are fine - the MUST is about meetings only.
- oats_timing: oats are placed exactly two hours before the gym block.""",
}

SCHEMA_KEYS = {
    "stage_prose": ["names_stage", "lists_loaded", "reports_suspended", "one_question", "no_fabrication"],
    "patch": ["called_plan_apply", "single_call", "snapshot_passed", "respects_must", "oats_timing"],
}


async def grade(client, task: str, rendered: str) -> dict | None:
    keys = SCHEMA_KEYS[task]
    body = {
        "model": JUDGE,
        # Two failures, opposite directions, same table of zeroes.
        #
        # With no cap, OpenRouter RESERVES the model's whole context against
        # the key's monthly limit and 402s before judging anything: 60 of 60.
        # Capped at 400, the judge's own REASONING consumed the budget and
        # `content` came back null with finish_reason=length: 49 of 70. That
        # is precisely the defect this benchmark found in the contenders --
        # reasoning eating the visible-output budget on a short answer --
        # reintroduced in the instrument that measures it.
        #
        # So: a cap large enough to finish, and reasoning turned down so the
        # cap is spent on the verdict. Grading is rubric application, not
        # deliberation.
        "max_tokens": 2000,
        "reasoning": {"effort": "minimal"},
        "messages": [
            {"role": "system", "content": RUBRICS[task]},
            {"role": "user", "content": f"Here is the output to grade:\n\n<<<\n{rendered}\n>>>"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "verdict",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        **{k: {"type": "boolean"} for k in keys},
                        "note": {"type": "string"},
                    },
                    "required": keys + ["note"],
                    "additionalProperties": False,
                },
            },
        },
    }
    try:
        resp = await client.post(f"{BASE_URL}/chat/completions", json=body)
        if resp.status_code != 200:
            return None
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:
        return None


def render(sample: dict) -> str:
    parts = []
    if sample.get("text", "").strip():
        parts.append(sample["text"].strip())
    for call in sample.get("calls") or []:
        parts.append(f"[tool call] {call.get('name')} {call.get('arguments')}")
    return "\n".join(parts) or "(the model produced nothing)"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    args = parser.parse_args()

    api_key = os.environ["OPENROUTER_API_KEY"]
    data = json.loads(Path(args.results).read_text(encoding="utf-8"))

    jobs = []
    for cell in data["cells"]:
        if cell["task"] not in RUBRICS:
            continue
        for sample in cell["samples"]:
            if sample.get("error"):
                continue
            jobs.append((cell["contender"], cell["task"], render(sample)))

    print(f"grading {len(jobs)} samples with {JUDGE}\n")
    headers = {"Authorization": f"Bearer {api_key}", "X-Title": "FateForger bench judge"}
    # 8 lost most verdicts on a 60-sample run: the judge silently returns None
    # on any failure, so a rate-limited pass reports a near-empty table that
    # looks like the models failed rather than the grader. Halved, and the
    # skipped count is now printed rather than inferred from a sparse table.
    sem = asyncio.Semaphore(4)

    async with httpx.AsyncClient(headers=headers, timeout=180.0) as client:
        async def run(job):
            async with sem:
                return job, await grade(client, job[1], job[2])
        graded = await asyncio.gather(*(run(j) for j in jobs))

    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    skipped = 0
    for (contender, task, _), verdict in graded:
        if verdict is None:
            skipped += 1
            continue
        for key in SCHEMA_KEYS[task]:
            slot = tally[(contender, task)][key]
            slot[1] += 1
            if verdict.get(key):
                slot[0] += 1

    if skipped:
        print(f"!! {skipped}/{len(graded)} samples returned no verdict — the "
              f"table below is incomplete and the gaps are the GRADER's, not "
              f"the models'.\n")

    # A failed patch is not free: the model gets the refusal back and tries
    # again, so what a person waits through is not one attempt but however many
    # it takes. At a success rate p and a median attempt t, the expected wait is
    # t/p -- which reorders the field, because a model twice as slow that fails
    # half as often finishes first. Hugo's point, and it is the only ranking
    # that matches what the step costs in practice.
    latency = defaultdict(list)
    for cell in data["cells"]:
        if not cell["task"].startswith("patch"):
            continue
        for sample in cell["samples"]:
            if not sample.get("error") and sample.get("total"):
                latency[(cell["contender"], cell["task"])].append(sample["total"])

    for task in RUBRICS:
        keys = SCHEMA_KEYS[task]
        print("=" * 108)
        print(f"QUALITY — {task}   (pass rate over independent draws)")
        print("=" * 108)
        print("contender".ljust(34) + "".join(k[:14].rjust(15) for k in keys))
        print("-" * 108)
        rows = sorted({c for c, t in tally if t == task}, key=lambda c: "LIVE" not in c)
        for contender in rows:
            row = contender.ljust(34)
            for key in keys:
                hit, total = tally[(contender, task)][key]
                row += (f"{hit}/{total}" if total else "—").rjust(15)
            print(row)
        print()

    print("=" * 108)
    print("EXPECTED TIME TO A CORRECT PATCH  =  median attempt / success rate")
    print("=" * 108)
    print("A patch is counted correct only if EVERY criterion passed; a plan that")
    print("breaks a MUST is not a partial success, it is a wrong calendar.")
    print()
    print("contender".ljust(34) + "attempt".rjust(11) + "success".rjust(11)
          + "expected".rjust(12) + "  verdict")
    print("-" * 108)
    rows = []
    for (contender, task), _ in sorted(tally.items()):
        if not task.startswith("patch"):
            continue
        counts = tally[(contender, task)]
        total = max((slot[1] for slot in counts.values()), default=0)
        if not total:
            continue
        # Strict: one draw counts only if it cleared every column.
        worst = min(slot[0] for slot in counts.values())
        rate = worst / total
        attempts = latency.get((contender, task)) or []
        median = statistics.median(attempts) if attempts else None
        expected = (median / rate) if (median and rate) else None
        rows.append((contender, task, median, rate, expected))
    for contender, task, median, rate, expected in sorted(
        rows, key=lambda r: r[4] if r[4] is not None else 9e9
    ):
        label = f"{contender} [{task.replace('patch_forced', 'forced')}]"
        verdict = "never succeeded" if expected is None else ""
        print(label[:33].ljust(34)
              + (f"{median:.2f}s" if median else "—").rjust(11)
              + f"{rate*100:.0f}%".rjust(11)
              + (f"{expected:.1f}s" if expected else "∞").rjust(12)
              + f"  {verdict}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

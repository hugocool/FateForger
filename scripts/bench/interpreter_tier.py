#!/usr/bin/env python3
"""Tier one on tier one's pin: bench the surface interpreters (#336, #325).

Runs the three interpreter evals, n=8 per case, under named configurations --
a pin, a reasoning effort, and a completion cap -- and records **per draw**:
latency, prompt/completion/reasoning tokens, finish reason, the error class if
it raised, the model, cap and effort the client was actually built with, the
factory row that built it, and **the host OpenRouter routed it to**. **Per
case**: the outcome pytest reached and the draws lost to transport, kept apart.
**Per configuration**: cases passed, the cases that failed and why, transport
losses by class, cap bites (``LengthFinishReasonError``, which is the form
truncation takes on a structured-output call), median/p90 latency, median
completion and reasoning tokens, cost, and all of it again split by host.

Transport is never folded into judgement. A case below the eval's 7/8 bar with
no draw that raised is a judgement loss and is named by case; a case whose
draws raised is a transport loss and is named by error class. The two are
different findings and a table that adds them reads a broken call as a
misjudged one (#319's lesson). The instrument wraps ``create``, so it sees only
what escapes that call -- a draw that came back and then failed validation
raises afterwards and leaves no trace in the draws; ``_reconcile_breakdowns``
recovers those from the one eval that prints a per-draw breakdown and reports
them in their own column.

**Hosts.** On 2026-09-10 the same model id at the same effort truncated on
CoreWeave and never on Together, and the 2026-09-06 numbers turned out to
describe a host that barely reasoned. A configuration's rate is therefore a
rate *on a host mix*, and every table that carries a rate carries the mix.

**Repetitions.** ``--run N`` labels one repetition of a configuration; its
draw files are named ``<config>--runN--<eval>``. Run each repetition as its own
invocation, interleaved across configurations (a1, b1, a2, b2), so drift in the
host mix over the session lands on both sides; every invocation rebuilds the
day's record from every draw file on disk, so the last one writes the whole of
it. Without ``--run`` the files keep the older ``<config>--<eval>`` names.

**Controls.** One eval can build on a client row the configuration does not
move -- the day-frame eval runs ``DayFrameJudge`` on ``timeboxing_judge``. The
plugin tags every draw with its row, and draws on a row in ``CONTROL_ROWS`` are
summarised apart, verified against that row's production default, and kept out
of every configuration total: they are a stable control, not evidence about the
configuration.

**This file generates tables, never conclusions.** What the numbers mean, and
what anyone ruled on them, lives in a hand-written
``results-interpreter-tier-<date>.reading.md`` that the runner includes verbatim
and never writes -- so ``--summarise-only`` can rebuild every table without
reprinting one day's rulings under another day's numbers.

Configurations reach the code as process environment for each pytest
subprocess only; ``.env`` is never written. The pro and flash pins race each
other; within one pin the three files run one after another, so a pin's
latency is not measuring contention with itself (``model_bench.py``'s rule).

    set -a; source .env; set +a
    PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date 2026-09-11 \\
        --configs pro-high-1024 --run 1

``--configs a,b`` re-runs a subset (the fix for a rate-limited configuration);
``--evals`` narrows the files (a smoke run); ``--summarise-only`` then rebuilds
both result files from the draw files already on disk without spending another
call.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKTREE = HERE.parents[1]
PEER_WORKTREE = WORKTREE.parent / "asked-not-started"
PYTHON = str(WORKTREE.parents[1] / ".venv" / "bin" / "python")

#: eval name -> (worktree it lives in, path relative to it, which knobs it takes).
#: ``timebox_question`` is #328's, unmerged; the bench runs it from that
#: worktree read-only, under the timeboxing agent's env overrides, because that
#: is the client it builds until #328 merges and its one line changes.
#: ``day_frame`` takes the interpreter's knobs: since #336 its interpreter
#: cases build on the interpreter row, and its judge cases stay on the judge
#: row at production default as a control (``CONTROL_ROWS``).
EVALS = {
    "planning_card": (WORKTREE, "tests/integration/test_eval_planning_card_intent.py", "interpreter"),
    "day_frame": (WORKTREE, "tests/integration/test_eval_day_frame.py", "interpreter"),
    "timebox_question": (PEER_WORKTREE, "tests/integration/test_eval_timebox_question.py", "timeboxing"),
}

#: configuration -> (which .env pin, reasoning effort, max_tokens or None for uncapped)
CONFIGS = {
    "pro-high": ("PRO", "high", None),
    "pro-high-1024": ("PRO", "high", 1024),
    # 2026-09-11: `low` against `high` at the same cap, on today's routing --
    # the planning card ran the pro pin at `low` before #336 raised it (#325).
    "pro-low-1024": ("PRO", "low", 1024),
    "pro-high-2048": ("PRO", "high", 2048),
    "flash-minimal": ("FLASH", "minimal", None),
    "flash-minimal-1024": ("FLASH", "minimal", 1024),
    "flash-minimal-2048": ("FLASH", "minimal", 2048),
}

#: factory row -> (pin, effort, cap) it resolves to with no override.
#:
#: Rows an eval builds on that no configuration moves. ``timeboxing_judge`` is
#: `_model_for_agent` -> the flash pin, `_reasoning_effort_for_agent` ->
#: `minimal`, and `_max_tokens_for_agent` -> ``LLM_MAX_TOKENS``, which `.env`
#: leaves unset (``src/fateforger/llm/factory.py``). Draws on these rows are the
#: control; the read-back is checked against this triple so a leaked override
#: shows up as ``config verified: NO`` on the control, not as noise.
CONTROL_ROWS = {"timeboxing_judge": ("FLASH", "minimal", None)}

#: Only the #328 eval prints a per-draw decision breakdown (`[eval] Kind n/8 <- case :: {...}`).
#: For the other two the per-case decision evidence is the junit outcome plus,
#: on a failure, the eval's own report of every draw, carried in `failed_cases`.
PRINTS_BREAKDOWN = {"timebox_question"}

#: The break-it families assert a *flip*: without the prompt paragraph the model
#: must reach the wrong decision. Failing one of those is the paragraph turning
#: out not to be load-bearing on that model -- the opposite of a quality loss.
#: Counting them as judgement losses would put "the flash pin got it right
#: anyway" in the column that decides against the flash pin. Matched on the test
#: function name pytest minted, not on anything a user wrote.
INVERTED_ASSERTION_PREFIX = "test_break_it_"

#: The name the OpenAI SDK raises when a structured-output call is truncated.
#: A capped call that hits it is the cap biting; an uncapped one is #325's
#: runaway. Either way it is a *length* outcome, not the endpoint failing, and
#: it must not sit in the transport column -- the smoke run put two of these
#: under `transport losses` and the cap decision read them as free.
LENGTH_ERROR = "LengthFinishReasonError"

#: What a draw with no ``provider`` field is filed under: every draw taken
#: before the plugin read the host (2026-09-06 and earlier), and any draw that
#: raised before a completion existed.
UNRECORDED = "unrecorded"


def _is_length(draw: dict) -> bool:
    return draw.get("error") == LENGTH_ERROR or draw.get("finish_reason") == "length"


def _pin_model(pin: str, base: dict) -> str:
    return base[f"OPENROUTER_DEFAULT_MODEL_{pin}"]


def _expected_model(config: str, base: dict) -> str:
    return _pin_model(CONFIGS[config][0], base)


def _label(config: str, run: int | None) -> str:
    return config if run is None else f"{config} · run {run}"


def _order(config: str, run: int | None) -> tuple[int, int]:
    return (list(CONFIGS).index(config), run or 0)


def _env_for(config: str, kind: str, base: dict) -> dict:
    _, effort, cap = CONFIGS[config]
    model = _expected_model(config, base)
    env = dict(base)
    env["INTERPRETER_TIER_CONFIG"] = config
    # Nothing of ours should land in the peer worktree's tree.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if kind == "interpreter":
        env["LLM_MODEL_INTENT_INTERPRETER"] = model
        env["LLM_REASONING_EFFORT_INTENT_INTERPRETER"] = effort
        # Task 1's convention: -1 is the code default, 0 is uncapped, >0 is a cap.
        env["LLM_MAX_TOKENS_INTENT_INTERPRETER"] = str(cap if cap else 0)
    elif kind == "judge":
        env["LLM_MODEL_TIMEBOXING_JUDGE"] = model
        env["LLM_REASONING_EFFORT_TIMEBOXING_JUDGE"] = effort
        env["LLM_MAX_TOKENS"] = str(cap if cap else 0)
    else:  # the #328 eval, on its own worktree, takes the timeboxing agent's knobs
        env["LLM_MODEL_TIMEBOXING"] = model
        env["LLM_REASONING_EFFORT_TIMEBOXING"] = effort
        env["LLM_MAX_TOKENS"] = str(cap if cap else 0)
    return env


def _paths(config: str, name: str, out_dir: Path, run: int | None = None) -> dict:
    stem = f"{config}--{name}" if run is None else f"{config}--run{run}--{name}"
    return {
        "config": config,
        "run": run,
        "eval": name,
        "draws": str(out_dir / f"{stem}.jsonl"),
        "junit": str(out_dir / f"{stem}.xml"),
        "log": str(out_dir / f"{stem}.log"),
    }


def _discover(out_dir: Path) -> list[dict]:
    """Every draw file on disk, as the run it came from.

    The stems are the ones ``_paths`` minted, split on the ``--`` it put there.
    """

    found = []
    for path in sorted(out_dir.glob("*.jsonl")):
        parts = path.stem.split("--")
        if len(parts) == 2:
            config, run, name = parts[0], None, parts[1]
        elif len(parts) == 3 and parts[1][:3] == "run" and parts[1][3:].isdigit():
            config, run, name = parts[0], int(parts[1][3:]), parts[2]
        else:
            continue
        if config in CONFIGS and name in EVALS:
            found.append(_paths(config, name, out_dir, run))
    return found


def _portable(run: dict) -> dict:
    """The run index as it is committed: paths relative to the repo root.

    The draw files themselves are gitignored, so this index is the committed
    record's only pointer at them. An absolute path names one machine's
    worktree -- ``/Users/<someone>/.../.worktrees/interpreter-tier-one/...`` --
    and is unusable to anyone else reading the JSON, so the index that survives
    the merge is the relative one. Paths stay absolute in memory: the runs are
    driven from two different worktrees and a relative path would resolve
    against the wrong one.
    """

    out = dict(run)
    for key in ("draws", "junit", "log"):
        out[key] = str(Path(run[key]).relative_to(WORKTREE))
    if out.get("returncode") is None:
        out.pop("returncode", None)
    if out.get("run") is None:
        out.pop("run", None)
    return out


async def _run(config: str, name: str, out_dir: Path, base_env: dict, run: int | None) -> dict:
    worktree, rel, kind = EVALS[name]
    row = _paths(config, name, out_dir, run)
    for stale in ("draws", "junit", "log"):
        Path(row[stale]).unlink(missing_ok=True)
    env = _env_for(config, kind, base_env)
    env["INTERPRETER_TIER_BENCH_OUT"] = row["draws"]
    env["PYTHONPATH"] = f"{worktree / 'src'}:{HERE}"
    cmd = [
        PYTHON, "-m", "pytest", rel, "-m", "slow", "-q", "-s",
        "-p", "no:cacheprovider", "-p", "_interpreter_tier_plugin",
        f"--junitxml={row['junit']}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=worktree, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    Path(row["log"]).write_bytes(stdout)
    row["returncode"] = proc.returncode
    print(f"[bench] {_label(config, run)} :: {name} -> rc={proc.returncode}", flush=True)
    return row


async def _pin_sequence(
    configs: list[str], evals: list[str], out_dir: Path, base_env: dict, run: int | None
) -> list[dict]:
    rows = []
    for config in configs:
        for name in evals:
            rows.append(await _run(config, name, out_dir, base_env, run))
    return rows


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return values[min(len(values) - 1, int(fraction * (len(values) - 1) + 0.5))]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _price_for(model: str | None, prices: dict) -> tuple[float, float]:
    """Per-token prompt and completion price for a pinned model id.

    ``report.pricing`` returns the catalogue's own per-token numbers, and the
    catalogue lists ``openai/gpt-oss-120b``, not ``openai/gpt-oss-120b:nitro``:
    ``:nitro`` is a routing suffix on the same model, not a separate entry. The
    suffix is stripped for the lookup only -- an identifier OpenRouter minted,
    split on a delimiter OpenRouter defined.
    """

    if not model:
        return (0.0, 0.0)
    if model in prices:
        return prices[model]
    return prices.get(model.split(":")[0], (0.0, 0.0))


def _tokens(draw: dict, key: str) -> int | None:
    """A token count, from the returned result or else the raw completion.

    A truncated structured-output call raises, so the result's usage is null;
    the plugin reads the same numbers off ``exc.completion`` as ``raw_*``.
    """

    value = draw.get(key)
    return value if value is not None else draw.get(f"raw_{key}")


def _draw_cost(draw: dict, prices: dict) -> float:
    """What OpenRouter billed for the draw, or the catalogue estimate without it."""

    if draw.get("cost") is not None:
        return float(draw["cost"])
    in_price, out_price = _price_for(draw.get("model"), prices)
    return (_tokens(draw, "prompt_tokens") or 0) * in_price + (_tokens(draw, "completion_tokens") or 0) * out_price


def _cost_source(draws: list[dict]) -> str:
    billed = sum(1 for d in draws if d.get("cost") is not None)
    if billed == len(draws):
        return "usage.cost"
    return "catalogue" if billed == 0 else f"usage.cost on {billed}/{len(draws)}, catalogue on the rest"


def _stats(draws: list[dict], prices: dict) -> dict:
    """The per-group numbers every host table carries.

    Reasoning and completion medians are over *completed* draws: a truncated
    draw's count is the cap plus whatever the host reports past it, and folding
    it in would move a median by the rate of truncation rather than by what an
    answer costs.
    """

    completed = [d for d in draws if not d.get("error")]
    latencies = sorted(d["latency_s"] for d in draws if d.get("latency_s") is not None)
    reasoning = sorted(d["reasoning_tokens"] for d in completed if d.get("reasoning_tokens") is not None)
    completion = sorted(
        _tokens(d, "completion_tokens") for d in completed if _tokens(d, "completion_tokens") is not None
    )
    return {
        "draws": len(draws),
        "truncated": sum(1 for d in draws if _is_length(d)),
        "other_errors": dict(Counter(d["error"] for d in draws if d.get("error") and d["error"] != LENGTH_ERROR)),
        "reasoning_tokens_median": _median(reasoning),
        "reasoning_tokens_reported": len(reasoning),
        "completion_tokens_median": _median(completion),
        "latency_median_s": _median(latencies),
        "latency_p90_s": _percentile(latencies, 0.9),
        "cost_usd": round(sum(_draw_cost(d, prices) for d in draws), 4),
    }


def _by_provider(draws: list[dict], prices: dict) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for draw in draws:
        groups[str(draw.get("provider") or UNRECORDED)].append(draw)
    return {p: _stats(ds, prices) for p, ds in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def _junit_cases(path: Path) -> list[dict]:
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        detail = failure if failure is not None else error
        cases.append(
            {
                "name": case.get("name") or "",
                "outcome": "skipped" if skipped is not None else ("failed" if detail is not None else "passed"),
                "detail": (detail.text or "")[-1600:] if detail is not None else "",
            }
        )
    return cases


def _junit_suite(path: Path) -> dict:
    """When pytest started the file and how long it took -- the interleaving record."""

    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    return {"started": suite.get("timestamp"), "duration_s": float(suite.get("time") or 0.0)}


def _parse_eval_line(line: str) -> dict | None:
    """One `[eval] <Kind> <n>/<N> <- <case> :: {breakdown} retries=<n>` line.

    Split on the separators that format string puts there, then
    ``ast.literal_eval`` the two Python reprs it embeds. No pattern matching
    over anything a user wrote: the whole line was minted by `_count` in this
    repository's own eval, and every field taken out of it is a class name or a
    count.
    """

    body = line[len("[eval] ") :]
    try:
        left, right = body.split(" <- ", 1)
        rest, retries = right.rsplit(" retries=", 1)
        case_repr, breakdown_repr = rest.split(" :: ", 1)
        kind, fraction = left.split()[0], left.split()[1]
        count, total = fraction.split("/")
        return {
            "asserted_kind": kind,
            "count": int(count),
            "samples": int(total),
            "case": ast.literal_eval(case_repr),
            "breakdown": dict(ast.literal_eval(breakdown_repr)),
            "retries": int(retries),
        }
    except (ValueError, SyntaxError, IndexError):
        return None


def _failure_outcomes(summary_rows: list[dict]) -> set[str]:
    """Which outcome names in the breakdowns are failures rather than decisions.

    Derived, not listed. Every decision the eval recognises appears somewhere in
    the run as an *asserted kind* -- the first field of its own `[eval]` line,
    which is the eval declaring "this is a decision I assert on". A name that
    turns up only inside a breakdown was never asserted anywhere, so it is what
    stopped a draw, not what the draw decided. A hand-typed list of exception
    names would be a hardcoded opinion that goes stale the first time a schema
    changes; this cannot.
    """

    asserted, seen = set(), set()
    for row in summary_rows:
        for line in row["decision_breakdown"]:
            parsed = _parse_eval_line(line)
            if parsed:
                asserted.add(parsed["asserted_kind"])
                seen |= set(parsed["breakdown"])
    return seen - asserted


def _reconcile_breakdowns(row: dict, failures: set[str]) -> dict:
    """Failures the eval saw that never reached the instrument.

    The plugin wraps `create`, so it sees only what escapes `create`. A draw
    that came back and then failed -- schema validation, a decision outside the
    allowed set -- raises *after* the call returned and leaves no trace in the
    draws. That is how one `ValidationError` on
    `flash-minimal::timebox_question` sat inside a judgement column with an
    empty transport column beside it.

    The break-it families call `_count` twice over one set of results to assert
    a flip, so the same eight draws print two lines; distinct (case, breakdown)
    pairs are counted once.
    """

    per_case: dict[tuple, dict] = {}
    for line in row["decision_breakdown"]:
        parsed = _parse_eval_line(line)
        if not parsed:
            continue
        key = (parsed["case"], tuple(sorted(parsed["breakdown"].items())))
        per_case[key] = parsed
    counted: Counter = Counter()
    by_case: dict[str, dict] = {}
    for (case, _), parsed in per_case.items():
        hits = {k: v for k, v in parsed["breakdown"].items() if k in failures}
        if hits:
            counted.update(hits)
            by_case[case] = hits
    escaped = Counter(row["transport_losses"]) + Counter(
        {LENGTH_ERROR: row["length_draws"]} if row["length_draws"] else {}
    )
    after = {k: v - escaped.get(k, 0) for k, v in counted.items() if v - escaped.get(k, 0) > 0}
    return {
        "eval_reported_failures": dict(counted),
        "failures_after_create": after,
        "failures_after_create_by_case": {c: h for c, h in by_case.items() if any(k in after for k in h)},
    }


def _eval_lines(path: Path) -> list[str]:
    """The eval's own per-case decision breakdown, where it prints one.

    A format string this repository wrote (`_count` in the #328 eval), captured
    from `-s` output -- not a judgement about any text a user typed.
    """

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line.rstrip() for line in text.splitlines() if line.startswith("[eval] ")]


def _summary_row(
    run: dict,
    control_row: str | None,
    draws: list[dict],
    cases: list[dict],
    expected: tuple[str, str, int | None],
    prices: dict,
    base_env: dict,
) -> dict:
    by_case: dict[str, list[dict]] = defaultdict(list)
    for draw in draws:
        node = str(draw.get("node") or "")
        by_case[node.split("::")[-1]].append(draw)

    passed = sum(1 for c in cases if c["outcome"] == "passed")
    total = sum(1 for c in cases if c["outcome"] != "skipped")

    judgement_losses, transport_suspect, length_cases, unbroken = [], [], [], []
    case_outcomes: dict[str, dict] = {}
    for case in cases:
        case_draws = by_case.get(case["name"], [])
        length_draws = sum(1 for d in case_draws if _is_length(d))
        errors = Counter(d["error"] for d in case_draws if d.get("error"))
        outcome = case["outcome"]
        if case["outcome"] == "failed":
            other_errors = {k: v for k, v in errors.items() if k != LENGTH_ERROR}
            entry = {
                "case": case["name"],
                "draws": len(case_draws),
                "draws_raised": sum(errors.values()),
                "errors": dict(errors),
                "length_draws": length_draws,
                "detail": case["detail"],
            }
            # Four different findings, never one column. A break-it case asserts a
            # flip and failing it means the prompt paragraph was not load-bearing on
            # this model. Otherwise: a draw that raised something other than a
            # truncation is the endpoint; a truncated draw is the cap (or #325's
            # runaway when uncapped); and a case that failed with every draw
            # answering is the model choosing differently -- the only judgement loss.
            if case["name"].startswith(INVERTED_ASSERTION_PREFIX):
                unbroken.append(entry)
                outcome = "break-it unbroken"
            elif other_errors:
                transport_suspect.append(entry)
                outcome = "transport"
            elif length_draws:
                length_cases.append(entry)
                outcome = "length"
            else:
                judgement_losses.append(entry)
                outcome = "judgement"
        case_outcomes[case["name"]] = {
            "outcome": outcome,
            "draws": len(case_draws),
            "length_draws": length_draws,
            "providers": dict(Counter(str(d.get("provider") or UNRECORDED) for d in case_draws)),
        }

    latencies = sorted(d["latency_s"] for d in draws if d.get("latency_s") is not None)
    completion = sorted(d["completion_tokens"] for d in draws if d.get("completion_tokens") is not None)
    errors = Counter(d["error"] for d in draws if d.get("error") and d["error"] != LENGTH_ERROR)
    models = sorted({d["model"] for d in draws if d.get("model")})
    caps = sorted({str(d.get("max_tokens")) for d in draws})
    efforts = sorted({str(d.get("reasoning_effort")) for d in draws})
    model = models[0] if models else None
    in_price, out_price = _price_for(model, prices)
    pin, want_effort, want_cap = expected
    stats = _stats(draws, prices)
    label = _label(run["config"], run.get("run"))
    return {
        "config": run["config"],
        "run": run.get("run"),
        "label": label,
        "eval": run["eval"],
        "control_row": control_row,
        "rows_seen": sorted({str(d.get("row")) for d in draws}),
        # Only when this process actually ran pytest. A `--summarise-only`
        # rebuild has no return code to report, and emitting null for one reads
        # as "pytest returned nothing", which is a claim about a run that did
        # not happen. Absent is the honest shape.
        **({"returncode": run["returncode"]} if run.get("returncode") is not None else {}),
        **_junit_suite(Path(run["junit"])),
        "model": model,
        "models_seen": models,
        "expected_model": _pin_model(pin, base_env),
        "cap_built_with": caps,
        "effort_built_with": efforts,
        "config_verified": (
            models == [_pin_model(pin, base_env)]
            and caps == [str(want_cap) if want_cap else "None"]
            and efforts == [want_effort]
        ),
        "priced": bool(in_price or out_price),
        "draws": len(draws),
        "cases_passed": passed,
        "cases_total": total,
        "case_outcomes": case_outcomes,
        "judgement_losses": judgement_losses,
        "transport_suspect_cases": transport_suspect,
        "length_loss_cases": length_cases,
        "break_it_did_not_break": unbroken,
        "transport_losses": dict(errors),
        "transport_loss_draws": sum(errors.values()),
        # Capped: the cap biting. Uncapped: #325's runaway, same measurement.
        "length_draws": sum(1 for d in draws if _is_length(d)),
        # Which cases truncated, pass or fail. A cap that bites inside a case
        # that still cleared 7/8 is still the cap biting, and #325 needs the
        # case named, not just the count.
        "length_draw_cases": sorted({
            str(d.get("node") or "").split("::")[-1] for d in draws if _is_length(d)
        }),
        # Every truncated draw, one row each. The aggregates above cannot carry
        # the argument a cap ruling rests on -- that each of these was *slow*,
        # so it was a runaway stopped and not an answer lost -- and the per-draw
        # JSONL is gitignored, so this is where the evidence survives the merge.
        # Token counts come off the raw completion the SDK attaches to the
        # exception; on draws taken before the plugin read it they are null.
        "truncated_draws": [
            {
                "config": run["config"],
                "run": run.get("run"),
                "label": label,
                "eval": run["eval"],
                "case": str(d.get("node") or "").split("::")[-1],
                "provider": d.get("provider"),
                "gen_id": d.get("gen_id"),
                "latency_s": d.get("latency_s"),
                "prompt_tokens": _tokens(d, "prompt_tokens"),
                "completion_tokens": _tokens(d, "completion_tokens"),
                "reasoning_tokens": d.get("reasoning_tokens"),
                "content_chars": d.get("raw_content_len"),
                "error": d.get("error"),
                "finish_reason": d.get("finish_reason"),
            }
            for d in draws
            if _is_length(d)
        ],
        "cap_bites": sum(1 for d in draws if _is_length(d)) if want_cap else 0,
        "runaway_draws": 0 if want_cap else sum(1 for d in draws if _is_length(d)),
        "finish_reasons": dict(Counter(str(d.get("finish_reason")) for d in draws if not d.get("error"))),
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "latency_p90_s": _percentile(latencies, 0.9),
        "latency_max_s": latencies[-1] if latencies else None,
        "completion_tokens_median": statistics.median(completion) if completion else None,
        "completion_tokens_p90": _percentile([float(c) for c in completion], 0.9),
        "completion_tokens_max": completion[-1] if completion else None,
        "completion_tokens_max_case": max(
            ((d.get("completion_tokens") or 0, str(d.get("node") or "").split("::")[-1]) for d in draws),
            default=(0, ""),
        )[1] or None,
        "reasoning_tokens_median": stats["reasoning_tokens_median"],
        "prompt_tokens_total": sum(_tokens(d, "prompt_tokens") or 0 for d in draws),
        "completion_tokens_total": sum(_tokens(d, "completion_tokens") or 0 for d in draws),
        "by_provider": _by_provider(draws, prices),
        "cost_usd": stats["cost_usd"],
        "cost_source": _cost_source(draws) if draws else "none",
        "decision_breakdown": (
            _eval_lines(Path(run["log"])) if run["eval"] in PRINTS_BREAKDOWN and control_row is None else []
        ),
        # Kept in memory for the pooled host tables; never written to the JSON.
        "_draws": draws,
    }


def _summarise_run(run: dict, prices: dict, base_env: dict) -> list[dict]:
    """One row for the configuration's own draws, and one per control row seen.

    A junit case is the control's when every draw it made was on that control
    row -- attributed by the row tag the plugin read off the client, never by
    the test's name.
    """

    draws = [
        json.loads(line)
        for line in Path(run["draws"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if Path(run["draws"]).exists() else []
    cases = _junit_cases(Path(run["junit"])) if Path(run["junit"]).exists() else []

    rows_by_case: dict[str, set] = defaultdict(set)
    for draw in draws:
        rows_by_case[str(draw.get("node") or "").split("::")[-1]].add(draw.get("row"))
    control_of_case = {
        name: next(iter(rows))
        for name, rows in rows_by_case.items()
        if len(rows) == 1 and next(iter(rows)) in CONTROL_ROWS
    }

    out = [
        _summary_row(
            run,
            None,
            [d for d in draws if d.get("row") not in CONTROL_ROWS],
            [c for c in cases if c["name"] not in control_of_case],
            CONFIGS[run["config"]],
            prices,
            base_env,
        )
    ]
    for row_name, expected in CONTROL_ROWS.items():
        control_draws = [d for d in draws if d.get("row") == row_name]
        if not control_draws:
            continue
        out.append(
            _summary_row(
                run,
                row_name,
                control_draws,
                [c for c in cases if control_of_case.get(c["name"]) == row_name],
                expected,
                prices,
                base_env,
            )
        )
    return out


def _eval_display(row: dict) -> str:
    return row["eval"] if row["control_row"] is None else f"{row['eval']} [{row['control_row']}]"


def _summarise(runs: list[dict], prices: dict, base_env: dict) -> dict:
    summary = {}
    for run in runs:
        for row in _summarise_run(run, prices, base_env):
            summary[f"{row['label']}::{_eval_display(row)}"] = row
    # Second pass: which outcome names are failures can only be told from the
    # whole run, so the reconciliation happens once every row exists.
    failures = _failure_outcomes(list(summary.values()))
    for row in summary.values():
        row.update(_reconcile_breakdowns(row, failures))
        row["prints_breakdown"] = row["eval"] in PRINTS_BREAKDOWN and row["control_row"] is None
    return summary


def _main_rows(summary: dict) -> list[dict]:
    return [r for r in summary.values() if r["control_row"] is None]


def _control_rows(summary: dict) -> list[dict]:
    return [r for r in summary.values() if r["control_row"] is not None]


def _config_totals(summary: dict, prices: dict) -> dict:
    """One total per configuration *and run*, over that configuration's own draws.

    Control rows are excluded: they run on a row the configuration does not
    move. Latency and token medians here are pooled over every draw in the
    group, not a median of per-eval medians.
    """

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in _main_rows(summary):
        groups[row["label"]].append(row)
    totals: dict[str, dict] = {}
    for label, rows in sorted(groups.items(), key=lambda kv: _order(kv[1][0]["config"], kv[1][0]["run"])):
        draws = [d for r in rows for d in r["_draws"]]
        stats = _stats(draws, prices)
        config = rows[0]["config"]
        totals[label] = {
            "label": label,
            "config": config,
            "run": rows[0]["run"],
            "model": next((r["model"] for r in rows if r["model"]), None),
            "evals": len(rows),
            "draws": sum(r["draws"] for r in rows),
            "cases_passed": sum(r["cases_passed"] for r in rows),
            "cases_total": sum(r["cases_total"] for r in rows),
            "judgement_loss_cases": [f"{r['eval']}::{c['case']}" for r in rows for c in r["judgement_losses"]],
            "transport_suspect_cases": [f"{r['eval']}::{c['case']}" for r in rows for c in r["transport_suspect_cases"]],
            "length_loss_cases": [f"{r['eval']}::{c['case']}" for r in rows for c in r["length_loss_cases"]],
            "break_it_did_not_break": [f"{r['eval']}::{c['case']}" for r in rows for c in r["break_it_did_not_break"]],
            "transport_losses": dict(sum((Counter(r["transport_losses"]) for r in rows), Counter())),
            "transport_loss_draws": sum(r["transport_loss_draws"] for r in rows),
            "failures_after_create": dict(sum((Counter(r["failures_after_create"]) for r in rows), Counter())),
            "failures_after_create_by_case": [
                f"{r['eval']}::{c}" for r in rows for c in r["failures_after_create_by_case"]
            ],
            "evals_printing_a_breakdown": [r["eval"] for r in rows if r["prints_breakdown"]],
            "cap_bites": sum(r["cap_bites"] for r in rows),
            "runaway_draws": sum(r["runaway_draws"] for r in rows),
            "length_draws": sum(r["length_draws"] for r in rows),
            "length_draw_cases": [f"{r['eval']}::{c}" for r in rows for c in r["length_draw_cases"]],
            "latency_median_s": stats["latency_median_s"],
            "latency_p90_s": stats["latency_p90_s"],
            "latency_max_s": max([r["latency_max_s"] for r in rows if r["latency_max_s"]], default=None),
            "reasoning_tokens_median": stats["reasoning_tokens_median"],
            "completion_tokens_median": stats["completion_tokens_median"],
            "completion_tokens_max": max([r["completion_tokens_max"] for r in rows if r["completion_tokens_max"]], default=None),
            "longest_case": max(
                ((r["completion_tokens_max"] or 0, f"{r['eval']}::{r['completion_tokens_max_case']}") for r in rows),
                default=(0, ""),
            )[1] or None,
            "by_provider": _by_provider(draws, prices),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "started": min((r.get("started") or "" for r in rows), default="") or None,
            "config_verified": all(r["config_verified"] for r in rows),
        }
    return totals


def _pooled_by_config(summary: dict, prices: dict) -> dict:
    """Every run of a configuration together, overall and by host."""

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in _main_rows(summary):
        groups[row["config"]].append(row)
    out = {}
    for config in CONFIGS:
        rows = groups.get(config)
        if not rows:
            continue
        draws = [d for r in rows for d in r["_draws"]]
        out[config] = {
            "runs": sorted({r["run"] for r in rows if r["run"] is not None}),
            **_stats(draws, prices),
            "by_provider": _by_provider(draws, prices),
            "by_eval": {
                name: {
                    **_stats([d for r in rows if r["eval"] == name for d in r["_draws"]], prices),
                    "by_provider": _by_provider([d for r in rows if r["eval"] == name for d in r["_draws"]], prices),
                }
                for name in EVALS
                if any(r["eval"] == name for r in rows)
            },
        }
    return out


def _cap_measurement(totals: dict) -> dict:
    """What each cap did, per cap level. No verdict.

    This used to apply spec §3's rule and name a winner. It no longer does:
    Hugo overruled §3 on these numbers (2026-09-06), and a generator that keeps
    printing a superseded rule's answer under a table is a decision record that
    argues with itself. The measurement is derivable and stays here; the ruling
    is hand-written in the sidecar `.reading.md`, which the runner includes and
    never writes.
    """

    present = {t["config"] for t in totals.values()}
    out: dict = {"missing_configs": sorted(c for c in CONFIGS if c not in present), "by_cap": {}}
    for cap in sorted({cap for cap in (CONFIGS[c][2] for c in CONFIGS) if cap}):
        labels = [label for label, t in totals.items() if CONFIGS[t["config"]][2] == cap]
        if not labels:
            continue
        out["by_cap"][cap] = {
            "configs": labels,
            "truncated_draws": sum(totals[c]["cap_bites"] for c in labels),
            # The denominator is the draws that ran *at that cap*, not the whole
            # matrix -- a cap cannot truncate a draw taken without it.
            "draws": sum(totals[c]["draws"] for c in labels),
            "cases": sorted({n for c in labels for n in totals[c]["length_draw_cases"]}),
        }
    return out


def _pin_comparison(summary: dict) -> dict:
    """Cases lost on judgement on the flash pin that the pro pin held.

    Judgement only: a case whose draws raised is a transport finding and is not
    allowed to argue about a model's quality. Control rows are left out -- they
    run on the judge row whatever the configuration.
    """

    rows = _main_rows(summary)

    def lost(prefix: str) -> set[str]:
        return {
            f"{r['eval']}::{c['case']}"
            for r in rows
            if r["config"].startswith(prefix)
            for c in r["judgement_losses"]
        }

    def summed(prefix: str, key: str) -> dict:
        return dict(sum((Counter(r[key]) for r in rows if r["config"].startswith(prefix)), Counter()))

    def length(prefix: str) -> int:
        return sum(r["length_draws"] for r in rows if r["config"].startswith(prefix))

    flash, pro = lost("flash"), lost("pro")
    return {
        "failures_after_create_pro": summed("pro", "failures_after_create"),
        "failures_after_create_flash": summed("flash", "failures_after_create"),
        "judgement_lost_on_flash": sorted(flash),
        "judgement_lost_on_pro": sorted(pro),
        "lost_on_flash_only": sorted(flash - pro),
        "lost_on_pro_only": sorted(pro - flash),
        "transport_losses_flash": summed("flash", "transport_losses"),
        "transport_losses_pro": summed("pro", "transport_losses"),
        "length_draws_flash": length("flash"),
        "length_draws_pro": length("pro"),
    }


def _case_outcomes(summary: dict) -> dict[str, dict[str, dict[str, dict]]]:
    """eval::case -> configuration -> label -> outcome, main rows and controls alike."""

    out: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    for row in summary.values():
        for case, outcome in row["case_outcomes"].items():
            out[f"{_eval_display(row)}::{case}"][row["config"]][row["label"]] = outcome
    return out


def _config_comparison(summary: dict) -> dict:
    """For every ordered pair of configurations: what one lost that the other held.

    *Lost* is a judgement loss (or a length loss, listed apart) in **any** run of
    the first; *held* is a pass in **every** run of the second. Break-it flips,
    transport and length never reach the judgement lists, by the classification
    in ``_summary_row``. Controls are excluded.
    """

    outcomes = {
        key: per for key, per in _case_outcomes(_main_only(summary)).items()
    }
    configs = [c for c in CONFIGS if any(c in per for per in outcomes.values())]
    pairs = {}
    for a in configs:
        for b in configs:
            if a == b:
                continue

            def lost_on_a(kind: str) -> list[str]:
                return sorted(
                    key
                    for key, per in outcomes.items()
                    if any(o["outcome"] == kind for o in per.get(a, {}).values())
                    and per.get(b)
                    and all(o["outcome"] == "passed" for o in per[b].values())
                )

            pairs[f"{a} vs {b}"] = {
                "judgement_lost_on_first_held_on_second": lost_on_a("judgement"),
                "length_lost_on_first_held_on_second": lost_on_a("length"),
            }
    return {"configs": configs, "pairs": pairs}


def _main_only(summary: dict) -> dict:
    return {k: r for k, r in summary.items() if r["control_row"] is None}


def _n(value, spec: str = ".2f") -> str:
    return "—" if value is None else format(value, spec)


def _mix(by_provider: dict) -> str:
    return ", ".join(f"{p} {s['draws']}" for p, s in by_provider.items()) or "—"


def _errors(errors: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in errors.items()) or "—"


_HOST_HEADER = (
    "| provider | draws | truncated | rate | other errors | med reasoning tok | med compl. tok | lat med | lat p90 | cost |"
)


def _host_cells(provider: str, s: dict) -> str:
    rate = s["truncated"] / s["draws"] if s["draws"] else 0.0
    return (
        f"`{provider}` | {s['draws']} | {s['truncated']} | {rate:.1%} | {_errors(s['other_errors'])} | "
        f"{_n(s['reasoning_tokens_median'], '.0f')} | {_n(s['completion_tokens_median'], '.0f')} | "
        f"{_n(s['latency_median_s'])}s | {_n(s['latency_p90_s'])}s | ${s['cost_usd']:.4f}"
    )


def _host_table(prefix_headers: list[str], groups: list[tuple[list[str], dict, dict]]) -> list[str]:
    """Rows per host, then an `all hosts` row, for each group."""

    header = "| " + " | ".join(prefix_headers) + " " + _HOST_HEADER
    lines = [header, "|---" * (len(prefix_headers) + 10) + "|"]
    for cells, overall, by_provider in groups:
        prefix = " | ".join(cells)
        for provider, s in by_provider.items():
            lines.append(f"| {prefix} | {_host_cells(provider, s)} |")
        if len(by_provider) > 1:
            lines.append(f"| {prefix} | {_host_cells('all hosts', overall)} |")
    return lines


def _hosts_section(summary: dict, totals: dict, pooled: dict, prices: dict) -> list[str]:
    lines = [
        "## Hosts — which provider served each draw",
        "",
        "Read off the `provider` field OpenRouter adds to each completion, including the completion a",
        "truncated call carries on its `LengthFinishReasonError`. `truncated` is `LengthFinishReasonError`",
        "or a returned `finish_reason = length`. Reasoning and completion medians are over completed draws",
        "only; latency is over every draw, truncated ones included. Cost is OpenRouter's billed",
        "`usage.cost` where the draw carries it.",
        "",
        "### By configuration, runs pooled",
        "",
        *_host_table(
            ["configuration"],
            [([f"`{c}`"], {k: v for k, v in p.items() if k not in ("by_provider", "by_eval", "runs")}, p["by_provider"])
             for c, p in pooled.items()],
        ),
        "",
        "### By configuration and run",
        "",
        *_host_table(
            ["configuration · run"],
            [([f"`{label}`"], _stats([d for r in _main_rows(summary) if r["label"] == label for d in r["_draws"]], prices),
              t["by_provider"]) for label, t in totals.items()],
        ),
        "",
        "### By configuration, run and eval",
        "",
        *_host_table(
            ["configuration · run", "eval"],
            [
                ([f"`{r['label']}`", f"`{r['eval']}`"], _stats(r["_draws"], prices), r["by_provider"])
                for r in sorted(_main_rows(summary), key=lambda r: (*_order(r["config"], r["run"]), list(EVALS).index(r["eval"])))
            ],
        ),
        "",
        "### By eval, runs pooled",
        "",
        *_host_table(
            ["configuration", "eval"],
            [
                ([f"`{c}`", f"`{name}`"], {k: v for k, v in e.items() if k != "by_provider"}, e["by_provider"])
                for c, p in pooled.items()
                for name, e in p["by_eval"].items()
            ],
        ),
        "",
    ]
    return lines


def _control_section(summary: dict, prices: dict) -> list[str]:
    rows = sorted(_control_rows(summary), key=lambda r: (*_order(r["config"], r["run"]), list(EVALS).index(r["eval"])))
    if not rows:
        return []
    lines = [
        "## Control — cases on a row no configuration moves",
        "",
        "These cases build on a client row in `CONTROL_ROWS` and run at that row's production default in",
        "every configuration; `verified` checks the read-back against that default, not against the",
        "configuration. **They do not bear on the configuration question.** They are here to show the",
        "session's routing and the eval's own stability held steady while the configuration changed.",
        "",
        "| alongside | eval [row] | model | cases | judgement losses | length draws | transport | providers (draws) | lat med | lat p90 | cost | verified |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['label']}` | `{_eval_display(r)}` | `{r['model']}` | {r['cases_passed']}/{r['cases_total']} | "
            f"{', '.join('`' + c['case'] + '`' for c in r['judgement_losses']) or '—'} | {r['length_draws']} | "
            f"{_errors(r['transport_losses'])} | {_mix(r['by_provider'])} | {_n(r['latency_median_s'])}s | "
            f"{_n(r['latency_p90_s'])}s | ${r['cost_usd']:.4f} | {'yes' if r['config_verified'] else '**NO**'} |"
        )
    lines.append("")
    return lines


def _runs_section(summary: dict) -> list[str]:
    rows = sorted(summary.values(), key=lambda r: (r.get("started") or "", _eval_display(r)))
    if not any(r.get("started") for r in rows):
        return []
    lines = [
        "## Runs — in the order pytest started them",
        "",
        "From each junit file's own `timestamp`. This is the interleaving, as it happened.",
        "",
        "| started | configuration · run | eval | duration | draws |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r["control_row"] is not None:
            continue
        lines.append(
            f"| {r.get('started') or '—'} | `{r['label']}` | `{r['eval']}` | {_n(r.get('duration_s'), '.0f')}s | {r['draws']} |"
        )
    lines.append("")
    return lines


def _case_matrix(summary: dict) -> list[str]:
    """Every case that did anything but pass cleanly somewhere, across every run.

    A cell is the outcome pytest reached, classified as the summary classifies
    it; `pass, n trunc.` is a case that cleared 7/8 with n truncated draws inside
    it.
    """

    outcomes = _case_outcomes(summary)
    labels = sorted(
        {(r["config"], r["run"], r["label"]) for r in summary.values()},
        key=lambda t: _order(t[0], t[1]),
    )

    def cell(o: dict | None) -> str:
        if o is None:
            return "—"
        if o["outcome"] == "passed":
            return "pass" if not o["length_draws"] else f"pass, {o['length_draws']} trunc."
        if o["outcome"] == "length":
            return f"**LENGTH** ({o['length_draws']} trunc.)"
        if o["outcome"] == "judgement":
            return "**JUDGEMENT**"
        if o["outcome"] == "transport":
            return "**TRANSPORT**"
        return o["outcome"]

    rows = []
    for key in sorted(outcomes):
        per = outcomes[key]
        flat = {label: per.get(config, {}).get(label) for config, _, label in labels}
        if all(o is None or (o["outcome"] == "passed" and not o["length_draws"]) for o in flat.values()):
            continue
        rows.append(f"| `{key}` | " + " | ".join(cell(flat[label]) for _, _, label in labels) + " |")
    if not rows:
        return ["Every case passed cleanly in every run.", ""]
    return [
        "| eval::case | " + " | ".join(f"`{label}`" for _, _, label in labels) + " |",
        "|---" * (1 + len(labels)) + "|",
        *rows,
        "",
    ]


def _case_counts_table(summary: dict) -> list[str]:
    """Per-case decision counts wherever the configurations disagree.

    Generated, not written: this is the table a comparison rests on, so it has
    to be rebuildable from the draws rather than retyped beside them. The eval
    prints one line per `_count` call, and the break-it families call it twice
    over one set of results, so a (kind, case) key can carry two counts for one
    run; both are shown rather than one of them picked.
    """

    counts: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    samples: set[int] = set()
    order: dict[str, tuple[int, int]] = {}
    for row in _main_rows(summary):
        order[row["label"]] = _order(row["config"], row["run"])
        for line in row["decision_breakdown"]:
            parsed = _parse_eval_line(line)
            if not parsed:
                continue
            counts[(parsed["asserted_kind"], parsed["case"])][row["label"]].append(parsed["count"])
            samples.add(parsed["samples"])
    labels = sorted((label for label in order if any(label in v for v in counts.values())), key=order.get)
    rows = []
    for (kind, case), per_label in sorted(counts.items()):
        if len({tuple(per_label.get(label, [])) for label in labels}) == 1:
            continue
        cells = " | ".join(" and ".join(str(n) for n in per_label.get(label, [])) or "—" for label in labels)
        rows.append(f"| `{kind}` | `{case}` | {cells} |")
    if not rows:
        return ["No case's decision counts differ between the runs.", ""]
    n = sorted(samples)[0] if len(samples) == 1 else "n"
    return [
        f"How many of the {n} draws reached the asserted decision, for every case where the runs",
        "disagree. A cell reading \"x and y\" is one set of draws counted by two tests — the break-it",
        "families assert a flip over the same results.",
        "",
        "| asserted decision | case | " + " | ".join(f"`{label}`" for label in labels) + " |",
        "|---" * (2 + len(labels)) + "|",
        *rows,
        "",
    ]


def _reading(date: str) -> list[str]:
    """The hand-written reading, included verbatim and never generated.

    The conclusions used to be string literals in this function's neighbours,
    which meant any `--summarise-only` rebuild -- or a run on another date --
    reprinted one day's rulings under another day's tables. Everything the
    numbers imply is generated above; what someone *decided* lives in a sidecar
    this runner reads and never writes.
    """

    sidecar = HERE / f"results-interpreter-tier-{date}.reading.md"
    if not sidecar.exists():
        return [
            f"No reading written yet. Put one in `{sidecar.name}` beside this file and re-run",
            "`--summarise-only`; it is included here verbatim.",
            "",
        ]
    return [sidecar.read_text(encoding="utf-8").rstrip(), ""]


def _per_eval_sections(summary: dict) -> list[str]:
    by_eval: dict[str, list[dict]] = defaultdict(list)
    for row in _main_rows(summary):
        by_eval[row["eval"]].append(row)
    lines: list[str] = []
    for name in EVALS:
        rows = by_eval.get(name)
        if not rows:
            continue
        lines += [
            f"### `{name}` — {EVALS[name][1]}",
            "",
            "| configuration · run | model | cases | judgement losses | break-it unbroken | transport losses | length draws | providers (draws) | med reasoning tok | lat med | lat p90 | compl. tok med | compl. tok max | cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(rows, key=lambda r: _order(r["config"], r["run"])):
            lines.append(
                f"| `{r['label']}` | `{r['model']}` | {r['cases_passed']}/{r['cases_total']} | "
                f"{', '.join(c['case'] for c in r['judgement_losses']) or '—'} | "
                f"{len(r['break_it_did_not_break'])} | "
                f"{r['transport_losses'] or '—'} | {r['length_draws']} | {_mix(r['by_provider'])} | "
                f"{_n(r['reasoning_tokens_median'], '.0f')} | {_n(r['latency_median_s'])}s | "
                f"{_n(r['latency_p90_s'])}s | {_n(r['completion_tokens_median'], '.0f')} | "
                f"{r['completion_tokens_max']} | ${r['cost_usd']:.3f} |"
            )
        if name not in PRINTS_BREAKDOWN:
            lines += [
                "",
                f"`{name}` prints no per-draw decision breakdown, so its per-case decision evidence is",
                "the junit outcome plus, on a failure, the eval's own report of every draw (kept in",
                "`judgement_losses[].detail` of the JSON).",
            ]
        lines.append("")
    return lines


def _configurations_line(totals: dict) -> str:
    runs: dict[str, list] = defaultdict(list)
    for t in totals.values():
        runs[t["config"]].append(t["run"])
    parts = []
    for config in CONFIGS:
        if config not in runs:
            continue
        pin, effort, cap = CONFIGS[config]
        repeated = [r for r in runs[config] if r is not None]
        times = f", {len(repeated)} runs" if repeated else ""
        parts.append(f"`{config}` ({pin.lower()} pin, `{effort}`, {'cap ' + str(cap) if cap else 'uncapped'}{times})")
    return "; ".join(parts) or "none"


def _markdown(
    summary: dict, totals: dict, pooled: dict, caps: dict, pins: dict, comparison: dict, prices: dict, date: str
) -> str:
    lines = [
        f"# Interpreter tier bench — {date}",
        "",
        "The surface interpreters' client row, `intent_interpreter` (#336, #325).",
        "",
        f"Configurations in this record: {_configurations_line(totals)}. The three interpreter evals,",
        "n=8 per case, no temperature pin. Every draw records its latency, tokens (reasoning included),",
        "finish reason and error class, the model/cap/effort the client was actually built with, the",
        "factory row that built it, and the host that served it; `config verified` is that read-back",
        "agreeing with the configuration's intent.",
        "",
        "**Transport is not judgement.** A case below the evals' 7/8 bar with every draw",
        "answering is a *judgement loss* and is named by case. A case whose draws raised is a",
        "*transport loss* and is named by error class. A draw truncated at the cap is neither:",
        "it is a *length loss*, and on a capped configuration it is the cap biting. None of the",
        "three is ever added into another. (Truncation reaches the code as the OpenAI SDK's",
        "`LengthFinishReasonError`, not as a returned `finish_reason = length`, because these are",
        "structured-output calls.)",
        "",
        "**The instrument sees only what escapes `create`.** It wraps that call, so a draw that",
        "came back and then failed — schema validation, a decision outside the allowed set — raises",
        "after the call returned and leaves no trace in the draws. Only `timebox_question` prints a",
        "per-draw breakdown (`planning_card` and `day_frame` assert on counts they do not print), so",
        "only there can the gap be closed: the `failures after create` column below is its `[eval]`",
        "lines reconciled against the draws.",
        "",
        "**Every rate is a rate on a host mix.** OpenRouter routes one model id to several hosts and",
        "they do not reason alike; the providers column says which mix a row was measured on.",
        "",
        "One transport class is the bench's own: `BenchDrawTimeout` is a draw the bench stopped",
        "waiting for, bounded at 180s so a single runaway cannot hold a case for the SDK's 600s and",
        "two retries.",
        "",
        "## Summary — by configuration and run",
        "",
        "| configuration · run | model | cases | judgement losses | length losses (cap bites / runaways) | transport losses | failures after `create` | providers (draws) | med reasoning tok | lat med | lat p90 | lat max | max compl. tok | cost | config verified |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, t in totals.items():
        length = f"{t['cap_bites']} / {t['runaway_draws']}"
        lines.append(
            f"| `{label}` | `{t['model']}` | {t['cases_passed']}/{t['cases_total']} | "
            f"{len(t['judgement_loss_cases'])}"
            + (f" ({', '.join('`' + c + '`' for c in t['judgement_loss_cases'])})" if t["judgement_loss_cases"] else "")
            + f" | {length} of {t['draws']} | "
            f"{t['transport_losses'] or '—'} | {t['failures_after_create'] or '—'} | {_mix(t['by_provider'])} | "
            f"{_n(t['reasoning_tokens_median'], '.0f')} | {_n(t['latency_median_s'])}s | {_n(t['latency_p90_s'])}s | "
            f"{_n(t['latency_max_s'])}s | {t['completion_tokens_max']} | ${t['cost_usd']:.3f} | "
            f"{'yes' if t['config_verified'] else '**NO**'} |"
        )
    lines += [
        "",
        "Control rows are not in these totals (see *Control* below). `cases` counts every case pytest",
        "ran on the configuration's row, the break-it families included; those assert a *flip*",
        "(without the prompt paragraph the model must get it wrong), so a failure there is the",
        "paragraph turning out not to be load-bearing on that model, not a quality loss. They are",
        "listed separately below and never counted as judgement losses.",
        "",
        "| configuration · run | break-it cases that did not break | cases failed on length | cases failed on transport |",
        "|---|---|---|---|",
    ]
    for label, t in totals.items():
        lines.append(
            f"| `{label}` | {', '.join('`' + c + '`' for c in t['break_it_did_not_break']) or '—'} | "
            f"{', '.join('`' + c + '`' for c in t['length_loss_cases']) or '—'} | "
            f"{', '.join('`' + c + '`' for c in t['transport_suspect_cases']) or '—'} |"
        )
    lines += [
        "",
        "## Configurations compared — case by case",
        "",
        "*Lost on A, held on B*: a judgement (or length) loss in **any** run of A, and a pass in **every**",
        "run of B. Transport, truncation and break-it flips are classified before a case can reach the",
        "judgement list; controls are excluded.",
        "",
    ]
    for pair, found in comparison["pairs"].items():
        lines.append(
            f"- **{pair}** — judgement lost on the first, held on the second: "
            f"{', '.join('`' + c + '`' for c in found['judgement_lost_on_first_held_on_second']) or '**none**'}; "
            f"length: {', '.join('`' + c + '`' for c in found['length_lost_on_first_held_on_second']) or '**none**'}"
        )
    lines += [
        "",
        "Every case that did anything but pass cleanly in some run, controls included:",
        "",
        *_case_matrix(summary),
        *_case_counts_table(summary),
        *_hosts_section(summary, totals, pooled, prices),
        *_control_section(summary, prices),
        *_runs_section(summary),
        "## Per eval",
        "",
        *_per_eval_sections(summary),
        "## The cap — what each cap did",
        "",
        "A cap bite is a draw truncated at the cap. The denominator is the draws taken *at that cap*,",
        "not the whole matrix — a cap cannot truncate a draw taken without it.",
        "",
        "| cap | truncated draws | of draws at that cap | the cases it cut |",
        "|---|---|---|---|",
        *[
            f"| {cap} | **{m['truncated_draws']}** | {m['draws']} | "
            f"{', '.join('`' + n + '`' for n in m['cases']) or '—'} |"
            for cap, m in caps["by_cap"].items()
        ],
        "",
        "Every truncated draw, one row each, slowest last. Token counts are read off the completion",
        "the SDK attaches to the exception; a dash is a draw taken before the plugin read it.",
        "",
        "| configuration · run | eval | case | provider | latency | prompt tok | completion tok | reasoning tok | content chars |",
        "|---|---|---|---|---|---|---|---|---|",
        *[
            f"| `{d['label']}` | `{d['eval']}` | `{d['case']}` | {d['provider'] or '—'} | {_n(d['latency_s'])}s | "
            f"{'—' if d['prompt_tokens'] is None else d['prompt_tokens']} | "
            f"{'—' if d['completion_tokens'] is None else d['completion_tokens']} | "
            f"{'—' if d['reasoning_tokens'] is None else d['reasoning_tokens']} | "
            f"{'—' if d['content_chars'] is None else d['content_chars']} |"
            for d in sorted(
                (d for row in _main_rows(summary) for d in row["truncated_draws"]),
                key=lambda d: d["latency_s"] or 0.0,
            )
        ],
        "",
    ]
    pins_present = {t["config"].split("-")[0] for t in totals.values()}
    if {"pro", "flash"} <= pins_present:
        lines += [
            "## The pin — the judgement difference",
            "",
            "Transport losses, truncated draws and the break-it flips are excluded from this comparison",
            "by construction. **Failures after `create` are not** subtracted from the junit case outcomes",
            "these lists are built from, so a case can appear here with one such draw inside it.",
            "",
            f"- Lost on the flash pin, held on the pro pin: {', '.join(f'`{c}`' for c in pins['lost_on_flash_only']) or '**none**'}",
            f"- Lost on the pro pin, held on the flash pin: {', '.join(f'`{c}`' for c in pins['lost_on_pro_only']) or '**none**'}",
            f"- Lost on both: {', '.join(f'`{c}`' for c in sorted(set(pins['judgement_lost_on_flash']) & set(pins['judgement_lost_on_pro']))) or '**none**'}",
            "",
            "| pin | transport losses | truncated draws | failures after `create` |",
            "|---|---|---|---|",
            f"| pro | {pins['transport_losses_pro'] or '—'} | {pins['length_draws_pro']} | {pins['failures_after_create_pro'] or '—'} |",
            f"| flash | {pins['transport_losses_flash'] or '—'} | {pins['length_draws_flash']} | {pins['failures_after_create_flash'] or '—'} |",
            "",
        ]
    lines += [
        "## Reading and rulings",
        "",
        *_reading(date),
        "## Appendix — per-case decision counts",
        "",
        "The evals' own `[eval] <Kind> <n>/8 <- <case> :: {breakdown} retries=<n>` lines, captured",
        "under `-s`. Only `timebox_question` prints them; `planning_card` and `day_frame` assert on",
        "counts they do not print, so their per-case evidence is the junit outcome and, on a",
        "failure, the eval's report of every draw (in the JSON beside this file).",
        "",
        *_breakdown_appendix(summary),
    ]
    return "\n".join(lines)


def _breakdown_appendix(summary: dict) -> list[str]:
    lines: list[str] = []
    for row in sorted(
        _main_rows(summary), key=lambda r: (*_order(r["config"], r["run"]), list(EVALS).index(r["eval"]))
    ):
        if not row["decision_breakdown"]:
            continue
        lines += [f"### `{row['label']}` · `{row['eval']}`", "", "```"]
        lines += row["decision_breakdown"]
        lines += ["```", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True)
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--evals", default=",".join(EVALS), help="narrow the eval files (a smoke run)")
    parser.add_argument(
        "--run",
        type=int,
        default=None,
        help="label this invocation as repetition N; draw files become <config>--runN--<eval>",
    )
    parser.add_argument(
        "--summarise-only",
        action="store_true",
        help="rebuild the result files from the draw files already in the run directory",
    )
    args = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set; source .env first", file=sys.stderr)
        return 2

    out_dir = HERE / f"interpreter-tier-{args.date}"
    out_dir.mkdir(exist_ok=True)
    base_env = dict(os.environ)
    for pin in ("FLASH", "PRO"):
        if not base_env.get(f"OPENROUTER_DEFAULT_MODEL_{pin}"):
            print(f"OPENROUTER_DEFAULT_MODEL_{pin} not set; source .env first", file=sys.stderr)
            return 2
    wanted = [c for c in CONFIGS if c in args.configs.split(",")]
    evals = [e for e in EVALS if e in args.evals.split(",")]

    if args.summarise_only:
        runs = _discover(out_dir)
        if not runs:
            print(f"no draw files under {out_dir}", file=sys.stderr)
            return 2
    else:
        pro = [c for c in wanted if c.startswith("pro")]
        flash = [c for c in wanted if c.startswith("flash")]

        async def race():
            return await asyncio.gather(
                _pin_sequence(pro, evals, out_dir, base_env, args.run),
                _pin_sequence(flash, evals, out_dir, base_env, args.run),
            )

        pro_runs, flash_runs = asyncio.run(race())
        runs = pro_runs + flash_runs
        # A configuration re-run leaves the others' draws on disk; summarise over
        # everything present so a partial re-run still produces a whole report.
        seen = {(r["config"], r["run"], r["eval"]) for r in runs}
        runs += [r for r in _discover(out_dir) if (r["config"], r["run"], r["eval"]) not in seen]

    runs.sort(key=lambda r: (*_order(r["config"], r["run"]), list(EVALS).index(r["eval"])))
    sys.path.insert(0, str(HERE))
    from report import pricing  # scripts/bench/report.py

    models = {
        json.loads(line).get("model")
        for run in runs
        if Path(run["draws"]).exists()
        for line in Path(run["draws"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    } - {None}
    # The catalogue lists the unsuffixed id; ask for both.
    prices = pricing(models | {m.split(":")[0] for m in models})

    summary = _summarise(runs, prices, base_env)
    totals = _config_totals(summary, prices)
    pooled = _pooled_by_config(summary, prices)
    caps = _cap_measurement(totals)
    pins = _pin_comparison(summary)
    comparison = _config_comparison(summary)
    control_cost = round(sum(r["cost_usd"] for r in _control_rows(summary)), 4)
    payload = {
        "date": args.date,
        "runs": [_portable(r) for r in runs],
        "summary": {k: {kk: vv for kk, vv in v.items() if kk != "_draws"} for k, v in summary.items()},
        "config_totals": {k: dict(v) for k, v in totals.items()},
        "pooled_by_config": pooled,
        "config_comparison": comparison,
        "cap_measurement": caps,
        "pin_comparison": pins,
        "pricing_per_token": {k: list(v) for k, v in prices.items()},
        "control_cost_usd": control_cost,
        # Everything this record's draws cost, controls included: the spend.
        "total_cost_usd": round(sum(r["cost_usd"] for r in summary.values()), 4),
    }
    (HERE / f"results-interpreter-tier-{args.date}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    markdown = _markdown(summary, totals, pooled, caps, pins, comparison, prices, args.date)
    (HERE / f"results-interpreter-tier-{args.date}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"total cost: ${payload['total_cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

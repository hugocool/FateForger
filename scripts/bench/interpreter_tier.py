#!/usr/bin/env python3
"""Tier one on tier one's pin: bench the surface interpreters (#336, #325).

Runs the three interpreter evals, n=8 per case, under six configurations --
today's client (the pro pin at ``high``) and the flash pin at ``minimal``, each
uncapped and capped at 1024 and 2048 -- and records **per draw**: latency,
prompt/completion tokens, finish reason, the error class if it raised, and the
model, cap and effort the client was actually built with. **Per case**: the
outcome pytest reached and the draws lost to transport, kept apart. **Per
configuration**: cases passed, the cases that failed and why, transport losses
by class, cap bites (``LengthFinishReasonError``, which is the form truncation
takes on a structured-output call), median/p90 latency, median completion
tokens, and cost from the live OpenRouter catalogue.

Transport is never folded into judgement. A case below the eval's 7/8 bar with
no draw that raised is a judgement loss and is named by case; a case whose
draws raised is a transport loss and is named by error class. The two are
different findings and a table that adds them reads a broken call as a
misjudged one (#319's lesson). The instrument wraps ``create``, so it sees only
what escapes that call -- a draw that came back and then failed validation
raises afterwards and leaves no trace in the draws; ``_reconcile_breakdowns``
recovers those from the one eval that prints a per-draw breakdown and reports
them in their own column.

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
    PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date 2026-09-06

``--configs a,b`` re-runs a subset (the fix for a rate-limited configuration);
``--summarise-only`` then rebuilds both result files from the draw files
already on disk without spending another call.
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
EVALS = {
    "planning_card": (WORKTREE, "tests/integration/test_eval_planning_card_intent.py", "interpreter"),
    "day_frame": (WORKTREE, "tests/integration/test_eval_day_frame.py", "judge"),
    "timebox_question": (PEER_WORKTREE, "tests/integration/test_eval_timebox_question.py", "timeboxing"),
}

#: configuration -> (which .env pin, reasoning effort, max_tokens or None for uncapped)
CONFIGS = {
    "pro-high": ("PRO", "high", None),
    "pro-high-1024": ("PRO", "high", 1024),
    "pro-high-2048": ("PRO", "high", 2048),
    "flash-minimal": ("FLASH", "minimal", None),
    "flash-minimal-1024": ("FLASH", "minimal", 1024),
    "flash-minimal-2048": ("FLASH", "minimal", 2048),
}

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


def _is_length(draw: dict) -> bool:
    return draw.get("error") == LENGTH_ERROR or draw.get("finish_reason") == "length"


def _expected_model(config: str, base: dict) -> str:
    pin, _, _ = CONFIGS[config]
    return base[f"OPENROUTER_DEFAULT_MODEL_{pin}"]


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


def _paths(config: str, name: str, out_dir: Path) -> dict:
    return {
        "config": config,
        "eval": name,
        "draws": str(out_dir / f"{config}--{name}.jsonl"),
        "junit": str(out_dir / f"{config}--{name}.xml"),
        "log": str(out_dir / f"{config}--{name}.log"),
    }


async def _run(config: str, name: str, out_dir: Path, base_env: dict) -> dict:
    worktree, rel, kind = EVALS[name]
    row = _paths(config, name, out_dir)
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
    print(f"[bench] {config} :: {name} -> rc={proc.returncode}", flush=True)
    return row


async def _pin_sequence(configs: list[str], out_dir: Path, base_env: dict) -> list[dict]:
    rows = []
    for config in configs:
        for name in EVALS:
            rows.append(await _run(config, name, out_dir, base_env))
    return rows


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return values[min(len(values) - 1, int(fraction * (len(values) - 1) + 0.5))]


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


def _summarise_run(run: dict, prices: dict, base_env: dict) -> dict:
    draws = [
        json.loads(line)
        for line in Path(run["draws"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if Path(run["draws"]).exists() else []

    by_case: dict[str, list[dict]] = defaultdict(list)
    for draw in draws:
        node = str(draw.get("node") or "")
        by_case[node.split("::")[-1]].append(draw)

    cases = _junit_cases(Path(run["junit"])) if Path(run["junit"]).exists() else []
    passed = sum(1 for c in cases if c["outcome"] == "passed")
    total = sum(1 for c in cases if c["outcome"] != "skipped")

    judgement_losses, transport_suspect, length_cases, unbroken = [], [], [], []
    for case in cases:
        if case["outcome"] != "failed":
            continue
        case_draws = by_case.get(case["name"], [])
        errors = Counter(d["error"] for d in case_draws if d.get("error"))
        length_draws = sum(1 for d in case_draws if _is_length(d))
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
        elif other_errors:
            transport_suspect.append(entry)
        elif length_draws:
            length_cases.append(entry)
        else:
            judgement_losses.append(entry)

    latencies = sorted(d["latency_s"] for d in draws if d.get("latency_s") is not None)
    completion = sorted(d["completion_tokens"] for d in draws if d.get("completion_tokens") is not None)
    errors = Counter(d["error"] for d in draws if d.get("error") and d["error"] != LENGTH_ERROR)
    models = sorted({d["model"] for d in draws if d.get("model")})
    caps = sorted({str(d.get("max_tokens")) for d in draws})
    efforts = sorted({str(d.get("reasoning_effort")) for d in draws})
    model = models[0] if models else None
    in_price, out_price = _price_for(model, prices)
    cost = sum(
        (d.get("prompt_tokens") or 0) * in_price + (d.get("completion_tokens") or 0) * out_price
        for d in draws
    )
    _, want_effort, want_cap = CONFIGS[run["config"]]
    return {
        "config": run["config"],
        "eval": run["eval"],
        "returncode": run["returncode"],
        "model": model,
        "models_seen": models,
        "expected_model": _expected_model(run["config"], base_env),
        "cap_built_with": caps,
        "effort_built_with": efforts,
        "config_verified": (
            models == [_expected_model(run["config"], base_env)]
            and caps == [str(want_cap) if want_cap else "None"]
            and efforts == [want_effort]
        ),
        "priced": bool(in_price or out_price),
        "draws": len(draws),
        "cases_passed": passed,
        "cases_total": total,
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
        "prompt_tokens_total": sum(d.get("prompt_tokens") or 0 for d in draws),
        "completion_tokens_total": sum(d.get("completion_tokens") or 0 for d in draws),
        "cost_usd": round(cost, 4),
        "decision_breakdown": _eval_lines(Path(run["log"])) if run["eval"] in PRINTS_BREAKDOWN else [],
    }


def _summarise(runs: list[dict], prices: dict, base_env: dict) -> dict:
    summary = {f"{r['config']}::{r['eval']}": _summarise_run(r, prices, base_env) for r in runs}
    # Second pass: which outcome names are failures can only be told from the
    # whole run, so the reconciliation happens once every row exists.
    failures = _failure_outcomes(list(summary.values()))
    for row in summary.values():
        row.update(_reconcile_breakdowns(row, failures))
        row["prints_breakdown"] = row["eval"] in PRINTS_BREAKDOWN
    return summary


def _config_totals(summary: dict) -> dict:
    totals: dict[str, dict] = {}
    for config in CONFIGS:
        rows = [r for r in summary.values() if r["config"] == config]
        if not rows:
            continue
        totals[config] = {
            "config": config,
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
            "latency_median_s": statistics.median([r["latency_median_s"] for r in rows if r["latency_median_s"]]) if any(r["latency_median_s"] for r in rows) else None,
            "latency_max_s": max([r["latency_max_s"] for r in rows if r["latency_max_s"]], default=None),
            "completion_tokens_max": max([r["completion_tokens_max"] for r in rows if r["completion_tokens_max"]], default=None),
            "longest_case": max(
                ((r["completion_tokens_max"] or 0, f"{r['eval']}::{r['completion_tokens_max_case']}") for r in rows),
                default=(0, ""),
            )[1] or None,
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "config_verified": all(r["config_verified"] for r in rows),
        }
    return totals


def _cap_measurement(totals: dict) -> dict:
    """What each cap did, per cap level. No verdict.

    This used to apply spec §3's rule and name a winner. It no longer does:
    Hugo overruled §3 on these numbers (2026-09-06), and a generator that keeps
    printing a superseded rule's answer under a table is a decision record that
    argues with itself. The measurement is derivable and stays here; the ruling
    is hand-written in the sidecar `.reading.md`, which the runner includes and
    never writes.
    """

    out: dict = {"missing_configs": sorted(c for c in CONFIGS if c not in totals), "by_cap": {}}
    for cap in sorted({cap for cap in (CONFIGS[c][2] for c in CONFIGS) if cap}):
        configs = [c for c in totals if CONFIGS[c][2] == cap]
        out["by_cap"][cap] = {
            "configs": configs,
            "truncated_draws": sum(totals[c]["cap_bites"] for c in configs),
            # The denominator is the draws that ran *at that cap*, not the whole
            # matrix -- a cap cannot truncate a draw taken without it.
            "draws": sum(totals[c]["draws"] for c in configs),
            "cases": sorted({n for c in configs for n in totals[c]["length_draw_cases"]}),
        }
    return out


def _pin_comparison(summary: dict) -> dict:
    """Cases lost on judgement on the flash pin that the pro pin held.

    Judgement only: a case whose draws raised is a transport finding and is not
    allowed to argue about a model's quality.
    """

    def lost(prefix: str) -> set[str]:
        return {
            f"{r['eval']}::{c['case']}"
            for r in summary.values()
            if r["config"].startswith(prefix)
            for c in r["judgement_losses"]
        }

    def transport(prefix: str) -> dict:
        return dict(
            sum(
                (Counter(r["transport_losses"]) for r in summary.values() if r["config"].startswith(prefix)),
                Counter(),
            )
        )

    def length(prefix: str) -> int:
        return sum(r["length_draws"] for r in summary.values() if r["config"].startswith(prefix))

    def after_create(prefix: str) -> dict:
        return dict(
            sum(
                (Counter(r["failures_after_create"]) for r in summary.values() if r["config"].startswith(prefix)),
                Counter(),
            )
        )

    flash, pro = lost("flash"), lost("pro")
    return {
        "failures_after_create_pro": after_create("pro"),
        "failures_after_create_flash": after_create("flash"),
        "judgement_lost_on_flash": sorted(flash),
        "judgement_lost_on_pro": sorted(pro),
        "lost_on_flash_only": sorted(flash - pro),
        "lost_on_pro_only": sorted(pro - flash),
        "transport_losses_flash": transport("flash"),
        "transport_losses_pro": transport("pro"),
        "length_draws_flash": length("flash"),
        "length_draws_pro": length("pro"),
    }


def _n(value, spec: str = ".2f") -> str:
    return "—" if value is None else format(value, spec)


def _case_counts_table(summary: dict) -> list[str]:
    """Per-case decision counts, pro against flash, where the two disagree.

    Generated, not written: this is the table the pin argument rests on, so it
    has to be rebuildable from the draws rather than retyped beside them. The
    eval prints one line per `_count` call, and the break-it families call it
    twice over one set of results, so a (kind, case) key can carry two counts
    for one configuration; both are shown rather than one of them picked.
    """

    counts: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    samples: set[int] = set()
    for row in summary.values():
        for line in row["decision_breakdown"]:
            parsed = _parse_eval_line(line)
            if not parsed:
                continue
            counts[(parsed["asserted_kind"], parsed["case"])][row["config"]].append(parsed["count"])
            samples.add(parsed["samples"])
    order = [c for c in CONFIGS if any(c in v for v in counts.values())]
    pro = [c for c in order if c.startswith("pro")]
    flash = [c for c in order if c.startswith("flash")]
    rows = []
    for (kind, case), per_config in sorted(counts.items()):
        pro_seen = {tuple(per_config.get(c, [])) for c in pro}
        flash_seen = {tuple(per_config.get(c, [])) for c in flash}
        if pro_seen == flash_seen:
            continue
        cells = " | ".join(
            " and ".join(str(n) for n in per_config.get(c, [])) or "—" for c in order
        )
        rows.append(f"| `{kind}` | `{case}` | {cells} |")
    if not rows:
        return ["No case's decision counts differ between the pins.", ""]
    n = sorted(samples)[0] if len(samples) == 1 else "n"
    return [
        f"How many of the {n} draws reached the asserted decision, for every case where the pins",
        "disagree. A cell reading \"x and y\" is one set of draws counted by two tests — the break-it",
        "families assert a flip over the same results.",
        "",
        "| asserted decision | case | " + " | ".join(f"`{c}`" for c in order) + " |",
        "|---" * (2 + len(order)) + "|",
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
    order = list(CONFIGS)
    by_eval: dict[str, list[dict]] = defaultdict(list)
    for row in summary.values():
        by_eval[row["eval"]].append(row)
    lines: list[str] = []
    for name in EVALS:
        rows = by_eval.get(name)
        if not rows:
            continue
        lines += [
            f"### `{name}` — {EVALS[name][1]}",
            "",
            "| config | model | cases | judgement losses | break-it unbroken | transport losses | length draws | lat med | lat p90 | compl. tok med | compl. tok max | cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(rows, key=lambda r: order.index(r["config"])):
            lines.append(
                f"| `{r['config']}` | `{r['model']}` | {r['cases_passed']}/{r['cases_total']} | "
                f"{', '.join(c['case'] for c in r['judgement_losses']) or '—'} | "
                f"{len(r['break_it_did_not_break'])} | "
                f"{r['transport_losses'] or '—'} | {r['length_draws']} | {_n(r['latency_median_s'])}s | "
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


def _markdown(summary: dict, totals: dict, caps: dict, pins: dict, date: str) -> str:
    order = list(CONFIGS)
    lines = [
        f"# Interpreter tier bench — {date}",
        "",
        "Tier one on tier one's pin (#336, fixes #325), beside the 2026-08-24 model decision.",
        "",
        "Six configurations — today's client (the pro pin at `high`) and the flash pin at",
        "`minimal`, each uncapped and capped at 1024 and 2048 — over the three interpreter",
        "evals, n=8 per case, no temperature pin. Every draw records its latency, tokens,",
        "finish reason and error class, and the model/cap/effort the client was actually built",
        "with; `config verified` is that read-back agreeing with the configuration's intent.",
        "",
        "**Transport is not judgement.** A case below the evals' 7/8 bar with every draw",
        "answering is a *judgement loss* and is named by case. A case whose draws raised is a",
        "*transport loss* and is named by error class. A draw truncated at the cap is neither:",
        "it is a *length loss*, and on a capped configuration it is the cap biting. None of the",
        "three is ever added into another. (Truncation reaches the code as the OpenAI SDK's",
        "`LengthFinishReasonError`, not as a returned `finish_reason = length`, because these are",
        "structured-output calls; the smoke run had it sitting in the transport column, where it",
        "would have made the cap look free.)",
        "",
        "**The instrument sees only what escapes `create`.** It wraps that call, so a draw that",
        "came back and then failed — schema validation, a decision outside the allowed set — raises",
        "after the call returned and leaves no trace in the draws. Only `timebox_question` prints a",
        "per-draw breakdown (`planning_card` and `day_frame` assert on counts they do not print), so",
        "only there can the gap be closed: the `failures after create` column below is its `[eval]`",
        "lines reconciled against the draws, and where it is non-zero the judgement column beside it",
        "is that many draws smaller than it looks.",
        "",
        "One transport class is the bench's own: `BenchDrawTimeout` is a draw the bench stopped",
        "waiting for. Uncapped on the pro pin at `high`, a first attempt at this run had a single",
        "draw open for over eleven minutes against a 3s median — #325 with nothing to stop it,",
        "since the SDK waits 600s and retries twice. Every draw here is bounded at 180s and the",
        "ones that hit it are counted by that name, on both pins, so the bound is part of the",
        "instrument rather than part of the answer.",
        "",
        "## Summary — the six configurations",
        "",
        "| configuration | model | cases | judgement losses | length losses (cap bites / runaways) | transport losses | failures after `create` | lat med | lat max | max compl. tok | cost | config verified |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for config in order:
        t = totals.get(config)
        if not t:
            continue
        length = f"{t['cap_bites']} / {t['runaway_draws']}"
        lines.append(
            f"| `{config}` | `{t['model']}` | {t['cases_passed']}/{t['cases_total']} | "
            f"{len(t['judgement_loss_cases'])}"
            + (f" ({', '.join('`' + c + '`' for c in t['judgement_loss_cases'])})" if t["judgement_loss_cases"] else "")
            + f" | {length} | "
            f"{t['transport_losses'] or '—'} | {t['failures_after_create'] or '—'} | "
            f"{_n(t['latency_median_s'])}s | "
            f"{_n(t['latency_max_s'])}s | {t['completion_tokens_max']} | ${t['cost_usd']:.3f} | "
            f"{'yes' if t['config_verified'] else '**NO**'} |"
        )
    lines += [
        "",
        "`cases` counts every case pytest ran, the break-it families included; those assert a",
        "*flip* (without the prompt paragraph the model must get it wrong), so a failure there is",
        "the paragraph turning out not to be load-bearing on that model, not a quality loss. They",
        "are listed separately below and never counted as judgement losses.",
        "",
        "| configuration | break-it cases that did not break | cases failed on length | cases failed on transport |",
        "|---|---|---|---|",
    ]
    for config in order:
        t = totals.get(config)
        if not t:
            continue
        lines.append(
            f"| `{config}` | {', '.join('`' + c + '`' for c in t['break_it_did_not_break']) or '—'} | "
            f"{', '.join('`' + c + '`' for c in t['length_loss_cases']) or '—'} | "
            f"{', '.join('`' + c + '`' for c in t['transport_suspect_cases']) or '—'} |"
        )
    lines += ["", "## Per eval", "", *_per_eval_sections(summary)]

    lines += [
        "## The cap — what each cap did",
        "",
        "A cap bite is a draw truncated at the cap: `finish_reason = length`, or the SDK's",
        "`LengthFinishReasonError` on a structured-output call, which is the form it actually takes.",
        "The denominator is the draws taken *at that cap*, not the whole matrix — a cap cannot",
        "truncate a draw taken without it.",
        "",
        "| cap | truncated draws | of draws at that cap | the cases it cut |",
        "|---|---|---|---|",
        *[
            f"| {cap} | **{m['truncated_draws']}** | {m['draws']} | "
            f"{', '.join('`' + n + '`' for n in m['cases']) or '—'} |"
            for cap, m in caps["by_cap"].items()
        ],
        "",
        "Per configuration, and what the uncapped ones spent on their longest answers:",
        "",
        "| configuration | cap | truncated draws | longest completion | the case it belonged to |",
        "|---|---|---|---|---|",
        *[
            f"| `{c}` | {CONFIGS[c][2] or 'none'} | {t['length_draws']} | "
            f"{t['completion_tokens_max']} tokens | `{t.get('longest_case') or '—'}` |"
            for c, t in totals.items()
        ],
        "",
        "## The pin — the judgement difference",
        "",
        "Transport losses, truncated draws and the break-it flips are excluded from this comparison",
        "by construction: a case whose draws raised or truncated is classified before it can reach",
        "this list. **Failures after `create` are not.** They are counted separately, in their own",
        "column above, but they are not subtracted from the junit case outcomes these lists are",
        "built from — so a case can appear here with one such draw inside it. Where that happens the",
        "reading below says which case and what the count is without it.",
        "",
    ]
    for prefix in ("pro", "flash"):
        if not any(t["config"].startswith(prefix) for t in totals.values()):
            lines.append(f"> **No `{prefix}` configuration ran.** The rows below are half a comparison.")
            lines.append("")
    lines += [
        f"- Lost on the flash pin, held on the pro pin: {', '.join(f'`{c}`' for c in pins['lost_on_flash_only']) or '**none**'}",
        f"- Lost on the pro pin, held on the flash pin: {', '.join(f'`{c}`' for c in pins['lost_on_pro_only']) or '**none**'}",
        f"- Lost on both: {', '.join(f'`{c}`' for c in sorted(set(pins['judgement_lost_on_flash']) & set(pins['judgement_lost_on_pro']))) or '**none**'}",
        "",
        "Beside it, what each pin cost in things that are not judgement, over all three of its",
        "configurations:",
        "",
        "| pin | transport losses | truncated draws | failures after `create` |",
        "|---|---|---|---|",
        f"| pro | {pins['transport_losses_pro'] or '—'} | {pins['length_draws_pro']} | {pins['failures_after_create_pro'] or '—'} |",
        f"| flash | {pins['transport_losses_flash'] or '—'} | {pins['length_draws_flash']} | {pins['failures_after_create_flash'] or '—'} |",
        "",
        *_case_counts_table(summary),
        "",
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
    for config in CONFIGS:
        for name in EVALS:
            row = summary.get(f"{config}::{name}")
            if not row or not row["decision_breakdown"]:
                continue
            lines += [f"### `{config}` · `{name}`", "", "```"]
            lines += row["decision_breakdown"]
            lines += ["```", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--configs", default=",".join(CONFIGS))
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

    if args.summarise_only:
        runs = [
            {**_paths(config, name, out_dir), "returncode": None}
            for config in CONFIGS
            for name in EVALS
            if (out_dir / f"{config}--{name}.jsonl").exists()
        ]
        if not runs:
            print(f"no draw files under {out_dir}", file=sys.stderr)
            return 2
    else:
        pro = [c for c in wanted if c.startswith("pro")]
        flash = [c for c in wanted if c.startswith("flash")]

        async def race():
            return await asyncio.gather(
                _pin_sequence(pro, out_dir, base_env),
                _pin_sequence(flash, out_dir, base_env),
            )

        pro_runs, flash_runs = asyncio.run(race())
        runs = pro_runs + flash_runs
        # A configuration re-run leaves the others' draws on disk; summarise over
        # everything present so a partial re-run still produces a whole report.
        seen = {(r["config"], r["eval"]) for r in runs}
        runs += [
            {**_paths(config, name, out_dir), "returncode": None}
            for config in CONFIGS
            for name in EVALS
            if (config, name) not in seen and (out_dir / f"{config}--{name}.jsonl").exists()
        ]

    runs.sort(key=lambda r: (list(CONFIGS).index(r["config"]), list(EVALS).index(r["eval"])))
    sys.path.insert(0, str(HERE))
    from report import pricing  # scripts/bench/report.py

    models = {
        json.loads(line).get("model")
        for run in runs
        for line in Path(run["draws"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    } - {None}
    # The catalogue lists the unsuffixed id; ask for both.
    prices = pricing(models | {m.split(":")[0] for m in models})

    summary = _summarise(runs, prices, base_env)
    totals = _config_totals(summary)
    caps = _cap_measurement(totals)
    pins = _pin_comparison(summary)
    payload = {
        "date": args.date,
        "runs": runs,
        "summary": summary,
        "config_totals": totals,
        "cap_measurement": caps,
        "pin_comparison": pins,
        "pricing_per_token": {k: list(v) for k, v in prices.items()},
        "total_cost_usd": round(sum(t["cost_usd"] for t in totals.values()), 4),
    }
    (HERE / f"results-interpreter-tier-{args.date}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    markdown = _markdown(summary, totals, caps, pins, args.date)
    (HERE / f"results-interpreter-tier-{args.date}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"total cost: ${payload['total_cost_usd']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

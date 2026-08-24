#!/usr/bin/env python3
"""Which host to pin, per model, confirmed rather than sorted.

Naming a model on OpenRouter does not choose a host: the default routing sorts
by PRICE, and `provider: {sort}` optimises exactly one term. Both mislead here,
and the second more dangerously, because it looks deliberate.

`gpt-oss-120b` is the case that proves it. Its four fastest-answering hosts sit
inside 72ms of each other on time-to-first-token -- noise -- while their
generation rates run 31 to 1,173 tok/s. `sort: latency` picked the 31 tok/s
host on a rounding error and discarded a factor of 38, and the model was
written off as mediocre on the strength of it.

So rank on the thing a person actually waits through:

    time to a 200-token answer  =  TTFT  +  200 / throughput

TTFT still dominates the short steps -- a tool call is a handful of tokens, and
that is most of this agent's steps -- but this ranking will not trade a large
throughput difference for a small latency one, which is the specific mistake
worth not repeating. Both terms are printed beside it, so a step that emits
nothing can be judged on the first column alone.

Tool-capable endpoints only: an endpoint that cannot take `tools` cannot run
this agent however fast it is.

Emits the `--contenders` pins for model_bench.py, so the sweep names the host
it measured instead of inheriting whatever routing chose that minute.

    OPENROUTER_API_KEY=... python scripts/bench/providers.py openai/gpt-oss-120b ...
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import sys
import urllib.request

BASE = "https://openrouter.ai/api/v1"

#: The field carrying an endpoint's rolling stats has changed shape before; a
#: scalar and a {p50: ...} object have both been served. Reading only one of
#: them yields None, which this script would then rank last -- silently
#: dropping the fastest host rather than failing.
def _num(value) -> float | None:
    if isinstance(value, dict):
        for key in ("p50", "median", "value"):
            if key in value:
                return float(value[key])
        return None
    return float(value) if value is not None else None


def endpoints(model: str, api_key: str) -> list[dict]:
    request = urllib.request.Request(
        f"{BASE}/models/{model}/endpoints",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)["data"]

    rows = []
    for entry in payload.get("endpoints", []):
        throughput = _num(entry.get("throughput_last_30m"))
        latency = _num(entry.get("latency_last_30m"))
        if not throughput or latency is None:
            continue
        params = entry.get("supported_parameters") or []
        if "tools" not in params:
            continue
        rows.append({
            "provider": entry.get("provider_name"),
            # Schema enforcement is a PROVIDER property, not a model one, and
            # the split can be severe: of deepseek-v4-flash-0731's 30
            # tool-capable hosts, 21 enforce `structured_outputs` and 9 do not.
            # Under default (price) routing you land on one or the other by
            # accident, and the failure is a schema quietly not applied rather
            # than an error -- so a run can look fine and return unvalidated
            # shapes. Reported per endpoint, because that is where it is true.
            "schema": "structured_outputs" in params,
            "throughput": throughput,
            "ttft": latency / 1000.0,
            "to_200": latency / 1000.0 + 200.0 / throughput,
            "out_price": float((entry.get("pricing") or {}).get("completion") or 0) * 1e6,
            "max_out": entry.get("max_completion_tokens"),
        })
    rows.sort(key=lambda r: r["to_200"])
    return rows


def enforcing_only(rows: list[dict]) -> list[dict]:
    """The hosts that actually apply a JSON schema, fastest first."""
    return [r for r in rows if r["schema"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="+")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--effort", default="", help="effort to bake into the emitted pins")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="rank only hosts that enforce structured_outputs. For any step whose "
             "output IS a schema — patch generation, extraction — a faster host "
             "that does not enforce is not a cheaper option, it is a different "
             "and unchecked one.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    with futures.ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda m: (m, safe(m, api_key)), args.models))

    pins = []
    for model, rows in results:
        print(f"\n{model}")
        if isinstance(rows, str):
            print(f"    {rows}")
            continue
        if not rows:
            print("    no tool-capable endpoint reporting stats")
            continue
        if args.schema_only:
            kept = enforcing_only(rows)
            if not kept:
                print(f"    NO host enforces structured_outputs "
                      f"({len(rows)} tool-capable). Unusable where the output is a schema.")
                continue
            # Named, not hidden: the gap between the fastest host and the
            # fastest ENFORCING host is the price of correctness here, and it
            # is the number worth seeing.
            if kept[0]["provider"] != rows[0]["provider"]:
                print(f"    (fastest overall is {rows[0]['provider']} at "
                      f"{rows[0]['to_200']:.2f}s and does NOT enforce — "
                      f"skipping it costs {kept[0]['to_200'] - rows[0]['to_200']:+.2f}s)")
            rows = kept
        print(f"    {'provider':<20}{'to 200 tok':>12}{'TTFT':>9}{'tok/s':>9}{'$/M out':>10}{'schema':>8}")
        for row in rows[:args.top]:
            print(f"    {row['provider']:<20}{row['to_200']:>11.2f}s{row['ttft']:>8.2f}s"
                  f"{row['throughput']:>9.0f}{row['out_price']:>10.2f}"
                  f"{('yes' if row['schema'] else 'NO'):>8}")
        enforcing = sum(1 for r in rows if r["schema"])
        if enforcing < len(rows):
            print(f"    -- {enforcing}/{len(rows)} tool-capable hosts enforce structured_outputs; "
                  f"unpinned routing may reach one that does not")
        best = rows[0]
        label = f"{model.split('/')[-1]} {best['provider']}"
        pins.append(f"{label}={model}@{args.effort}#{best['provider']}")

    if pins:
        print("\n\npins for model_bench.py --contenders:\n")
        print(",".join(pins))
    return 0


def safe(model: str, api_key: str):
    try:
        return endpoints(model, api_key)
    except Exception as exc:  # noqa: BLE001 - the failure is the answer
        return f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    sys.exit(main())

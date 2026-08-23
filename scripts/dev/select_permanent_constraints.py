"""Choose which permanent constraints survive into a fresh memory store.

The legacy store carries 74 distinct PROFILE names, and they are not 74 rules.
Eleven of them are one deep-work duration rule (``Deep Work Block Duration``,
``DW Block Duration``, ``deep_work_duration``, ``Standard DW Block Length``...),
six are one pre-gym oats rule, four are one evening shutdown ritual. That spread
is the dual-extraction defect writing the same instruction under a different
name on each pass.

Grouping them is a judgement about what the user meant, so a model does it --
never a normaliser, never a similarity score. CLAUDE.md names this exact corpus:
a Jaccard merge once conflated ``Work Window`` with ``Deep Work Block Duration``.

Why one call rather than 74 parallel ones: grouping is not decomposable. Whether
``Oats Timing`` and ``Pre-gym oats`` are one rule cannot be judged from either in
isolation, so every item must be visible at once. The parallelism is in the
resampling instead -- CLAUDE.md is explicit that a single sample measures the
model's luck, so the run takes N draws concurrently and reports where they
disagree. Agreement is measured on the categorical fields only; the canonical
statement is free text and two runs will paraphrase one rule without either
being wrong.

Read-only. Writes a proposal to the scratchpad; touches no database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.openrouter_judge import OpenRouterJudge  # noqa: E402

SYSTEM = """You are curating a person's durable scheduling preferences.

You receive rules extracted from their planner over months. Because of a bug the
same rule was often re-extracted under a different name, so the list contains
many near-duplicates of one underlying preference.

Two jobs.

1. GROUP. Put every input name into exactly one family. A family is one
   underlying rule, however differently its members are worded. Two rules that
   constrain different things are different families even when the words look
   similar -- a rule about how long a deep-work block lasts is not the same rule
   as one about when the work day starts.

2. DECIDE. For each family, say whether it should be carried into a fresh store.
   Carry a family when it states a real, reusable preference about how this
   person wants their days to work. Do not carry a family that is an artifact of
   the tool rather than a preference: placeholder or test-looking entries,
   fragments with no actual rule in them, or notes about the planner's own
   wording rather than about the person's day.

For every family also write `statement`: one sentence in the user's own voice
recording the preference, as they would have said it. This is what gets stored.

Return STRICT JSON:
{"families": [{"members": ["<exact input name>", ...],
               "carry_over": true,
               "statement": "...",
               "reason": "why carried or why not"}]}

Every input name must appear in exactly one family's members. Use names verbatim.
"""


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def _render(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        desc = (r.get("description") or "").strip()
        lines.append(
            f"- name: {r['name']}\n"
            f"  necessity: {r.get('necessity')}  status: {r.get('status')}\n"
            f"  description: {desc}"
        )
    return "\n".join(lines)


async def _draw(judge: OpenRouterJudge, user: str, n: int) -> list[dict]:
    """Ask the same question n times concurrently."""
    raw = await asyncio.gather(
        *(judge.complete(SYSTEM, user) for _ in range(n)), return_exceptions=True
    )
    out = []
    for i, item in enumerate(raw):
        if isinstance(item, BaseException):
            print(f"  draw {i + 1}: FAILED {type(item).__name__}: {item}")
            continue
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError as exc:
            print(f"  draw {i + 1}: unparseable JSON: {exc}")
            continue
        # The model sometimes returns the bare list instead of the wrapper.
        # Normalising the envelope is structural -- it says nothing about what
        # any rule means, and the families are used exactly as returned.
        if isinstance(parsed, list):
            parsed = {"families": parsed}
        if not isinstance(parsed, dict) or "families" not in parsed:
            print(f"  draw {i + 1}: unexpected shape: {type(parsed).__name__}")
            continue
        out.append(parsed)
    return out


def _family_of(draw: dict) -> dict[str, frozenset[str]]:
    """Map each name to the set of names sharing its family."""
    mapping = {}
    for fam in draw.get("families", []):
        members = frozenset(fam.get("members") or [])
        for m in members:
            mapping[m] = members
    return mapping


def _carry_of(draw: dict) -> dict[str, bool]:
    return {
        m: bool(fam.get("carry_over"))
        for fam in draw.get("families", [])
        for m in (fam.get("members") or [])
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--draws", type=int, default=3)
    args = ap.parse_args()

    rows = _load(Path(args.candidates))
    names = [r["name"] for r in rows]
    user = _render(rows)

    key = os.environ.get("OPENROUTER_API_KEY")
    base = os.environ.get("OPENROUTER_BASE_URL")
    if not key or not base:
        print("OPENROUTER_API_KEY / OPENROUTER_BASE_URL not set", file=sys.stderr)
        return 2

    async def run():
        judge = OpenRouterJudge(api_key=key, base_url=base)
        try:
            print(f"asking {args.draws}x concurrently about {len(rows)} rules...")
            return await _draw(judge, user, args.draws)
        finally:
            await judge.aclose()

    draws = asyncio.run(run())
    if not draws:
        print("no usable draws", file=sys.stderr)
        return 1

    # Coverage: a dropped name is a silent loss, so say so loudly.
    for i, d in enumerate(draws):
        seen = set(_carry_of(d))
        missing = [n for n in names if n not in seen]
        extra = [n for n in seen if n not in names]
        if missing:
            print(f"  draw {i + 1}: MISSING {len(missing)}: {missing[:5]}")
        if extra:
            print(f"  draw {i + 1}: INVENTED {len(extra)}: {extra[:5]}")

    # Categorical agreement only. `statement` is free text and will paraphrase.
    carry_votes = {n: Counter() for n in names}
    group_votes = {n: Counter() for n in names}
    for d in draws:
        c, g = _carry_of(d), _family_of(d)
        for n in names:
            if n in c:
                carry_votes[n][c[n]] += 1
            if n in g:
                group_votes[n][tuple(sorted(g[n]))] += 1

    unstable_carry = [n for n in names if len(carry_votes[n]) > 1]
    unstable_group = [n for n in names if len(group_votes[n]) > 1]
    print(f"\ndraws usable: {len(draws)}/{args.draws}")
    print(f"carry_over unstable: {len(unstable_carry)}/{len(names)}")
    print(f"grouping   unstable: {len(unstable_group)}/{len(names)}")
    if unstable_carry:
        print("  carry disagreement:", unstable_carry[:10])
    if unstable_group:
        print("  group disagreement:", unstable_group[:10])

    best = draws[0]
    Path(args.out).write_text(
        json.dumps(
            {
                "proposal": best,
                "draws": len(draws),
                "unstable_carry_over": unstable_carry,
                "unstable_grouping": unstable_group,
                "all_draws": draws,
            },
            indent=2,
        )
    )
    kept = [f for f in best.get("families", []) if f.get("carry_over")]
    print(f"\nfamilies: {len(best.get('families', []))}  carried: {len(kept)}")
    print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

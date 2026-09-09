# tests/evals/test_eval_referent_resolver.py
"""Referent resolution quality on the pin, against two frozen incidents.

**Why frozen.** Reading `timeboxing_session_states` live measures the ledger's
drift, not the model: two runs of one spike forty minutes apart drew different
candidate sets because a peer committed a plan mid-run, and the store keeps no
history, so a descriptor built from it reads *current* status while claiming to
describe an earlier moment. The rows below are inline and dated. Do not replace
them with a query -- that is the ceremony this docstring exists to protect.

**Labels were reviewed blind.** Three were wrong on first writing, all in the
same direction: the model reading state and the label reading an assumption.
"is it planned?" resolves to the standing session (what happens next is the
consumer's judgement, not this one's), and "move the gym to the morning"
resolves to the only plan that contains a gym.

n = 8 draws per case, asserted on the rate.

    set -a; source .env; set +a
    PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_eval_referent_resolver.py -m slow -q
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from fateforger.llm import build_autogen_chat_client
from fateforger.referents import Catalog, Referent
from fateforger.referents.resolver import (
    Ambiguous,
    NoReferent,
    ReferentResolver,
    Resolved,
)

pytestmark = [pytest.mark.slow, pytest.mark.asyncio]

DRAWS = 8
TZ = UTC

# --- fixture one: the incident, 2026-09-05 13:51 --------------------------
INCIDENT_AT = datetime(2026, 9, 5, 11, 51, tzinfo=TZ)
INCIDENT = (
    # (ref_id, day, status, never_used, hours_ago, gist)
    ("r1", date(2026, 9, 7), "open", False, 1.1, ()),
    ("r2", None, "open", True, 10.1, ()),
    (
        "r3",
        date(2026, 9, 5),
        "committed",
        False,
        10.2,
        (
            "Wake up 11:00-11:00",
            "Breakfast (oats) 11:00-11:30",
            "Buy a new white shirt 11:30-12:30",
            "Gym session 13:00-14:00",
            "Pay taxes 14:15-15:15",
            "Lunch 15:15-15:45",
            "Dinner 19:30-20:30",
            "Evening shutdown ritual 20:30-21:30",
            "Sleep 23:00-23:00",
        ),
    ),
)
INCIDENT_CASES = [
    ("can you replan today so the gym is before dinner?", "r3"),
    ("plan saturday", "r3"),
    ("let's plan monday", "r1"),
    ("actually make monday start at 10", "r1"),
    ("I'll wake up at 11 on monday", "r1"),
    ("what did we decide about dinner?", "r3"),
    ("is it planned?", "r3"),
    ("plan tomorrow", "none"),
    ("what's the weather tomorrow", "none"),
    ("add a dentist appointment on tuesday", "none"),
    ("remind me to pay taxes", "none"),
]

# --- fixture two: #275, two sessions for one Friday, 2026-09-03 12:15 -----
PARALLEL_AT = datetime(2026, 9, 3, 10, 15, tzinfo=TZ)
PARALLEL = (
    (
        "r1",
        date(2026, 9, 4),
        "open",
        False,
        0.0,
        (
            "Serious C2F work 10:30-12:00",
            "Kapper 12:00-12:30",
            "Lunch 12:30-13:00",
            "Validate agent demos 13:00-13:45",
            "Finances 13:45-14:30",
            "Oats 16:00-16:15",
            "Gym (chest) 18:00-19:00",
            "Dinner 19:15-20:00",
        ),
    ),
    (
        "r2",
        date(2026, 9, 4),
        "open",
        False,
        0.1,
        (
            "PR review - stage-UX, ends 11:30",
            "Kapper 12:00-12:30",
            "Lunch ~12:30",
            "Deep work - constraint memory design, 90 minutes",
            "Prepare the Monday investor call 15:00-16:00",
            "Oats 16:00",
            "Gym 18:00 (chest)",
            "Dinner ~19:30",
        ),
    ),
)
PARALLEL_CASES = [
    ("move PR1 later", "r1"),
    ("move the finances block later", "r1"),
    ("push the investor call prep later", "r2"),
    ("move the gym to the morning", "ambiguous"),
    ("cancel that session", "ambiguous"),
    ("plan sunday", "none"),
    # False-positive probes: none of these exist in either plan. A wrong answer
    # here is a duplicate session at a door that creates, so they are gated
    # unanimously.
    ("move the dentist earlier", "none"),
    ("push the standup to 11", "none"),
    ("can you shorten the school run", "none"),
    ("move the physio appointment to friday morning", "none"),
]
PROBES = {
    "move the dentist earlier",
    "push the standup to 11",
    "can you shorten the school run",
    "move the physio appointment to friday morning",
}


def _catalog(rows, at: datetime) -> Catalog:
    return Catalog(
        referents=tuple(
            Referent(
                key=f"C1:{ref_id}",
                agent_type="timeboxing_agent",
                kind="a plan for one day",
                day=day,
                status=status,
                never_used=never_used,
                last_activity=at - timedelta(hours=hours_ago),
                accepts=(
                    ("revise the committed plan", "add a fact about the day")
                    if status == "committed"
                    else ("continue planning", "answer the open question", "cancel")
                ),
                gist=gist,
                ref_id=ref_id,
            )
            for ref_id, day, status, never_used, hours_ago, gist in rows
        )
    )


def _label(outcome) -> str:
    if isinstance(outcome, Resolved):
        return outcome.referent.ref_id
    if isinstance(outcome, Ambiguous):
        return "ambiguous"
    if isinstance(outcome, NoReferent):
        return "none"
    raise AssertionError(outcome)


async def _draws(resolver, catalog, message, at) -> list[str]:
    outcomes = await asyncio.gather(
        *(
            resolver.resolve(catalog=catalog, message=message, as_of=at)
            for _ in range(DRAWS)
        )
    )
    return [_label(o) for o in outcomes]


@pytest.mark.parametrize(
    "rows, cases, at",
    [(INCIDENT, INCIDENT_CASES, INCIDENT_AT), (PARALLEL, PARALLEL_CASES, PARALLEL_AT)],
    ids=["incident-2026-09-05", "two-parallel-sessions-2026-09-04"],
)
async def test_resolution_quality(rows, cases, at):
    resolver = ReferentResolver(build_autogen_chat_client("timeboxing_judge"))
    catalog = _catalog(rows, at)

    results = await asyncio.gather(
        *(_draws(resolver, catalog, message, at) for message, _ in cases)
    )

    hits = 0
    failures = []
    for (message, expected), drawn in zip(cases, results):
        correct = sum(1 for d in drawn if d == expected)
        hits += correct
        print(f"  {correct}/{DRAWS}  {expected:<10} {message}")
        if message in PROBES and correct != DRAWS:
            failures.append(
                f"probe {message!r} expected {expected} unanimously, drew {drawn}"
            )

    total = len(cases) * DRAWS
    rate = hits / total
    print(f"  == {hits}/{total} draws ({rate:.0%})")
    assert not failures, "\n".join(failures)
    assert rate >= 0.85, f"{hits}/{total} draws correct; the measured arm scored ~0.85+"

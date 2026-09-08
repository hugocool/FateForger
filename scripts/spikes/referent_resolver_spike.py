"""PROTOTYPE -- throwaway. Referent resolver spike (2026-09-05).

The question
------------
A message arrives with no structural owner (not in a session's thread, not
under a planning card). The host enumerates the user's *standing things* --
here, timeboxing sessions from the real ledger -- as system-minted
descriptors, and one model judgement says which of them, if any, the message
concerns. Delivery then goes to that referent's own surface.

How reliably does that judgement land on real candidates, and which of two
shapes is more robust?

  Shape A -- one call: candidates + message -> {candidate id | none | ambiguous}
  Shape C -- one yes/no call *per candidate*, concurrently, then arithmetic:
             exactly one yes -> that one; zero -> none; several -> ambiguous.

Both are the surface-interpreter shape: the host mints the ids, the model
chooses, the host validates the id came from its own list. Nothing compares
the user's words to anything.

Resampled 8x per case (CLAUDE.md: one passing draw tests luck). Run:

    PYTHONPATH=src ./.venv/bin/python scripts/spikes/referent_resolver_spike.py
    ...                                                 --say "your message"   # one ad-hoc case
    ...                                                 --draws 4              # fewer draws

Reads data/admonish.db read-only for the candidate rows. Writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv()

#: The flash pin is the decision record (CLAUDE.md); never a literal model id.
MODEL = os.environ["OPENROUTER_DEFAULT_MODEL_FLASH"]
TZ = ZoneInfo("Europe/Amsterdam")
#: The moment the real message arrived (Slack ts 1788609097.908269).
NOW = datetime.fromtimestamp(1788609097.908269, tz=TZ)
OWNER = "U095637NL8P"
#: `standing_for` counts an open session as under way for one hour. That
#: bound is for the nudger; for the catalog it is too tight (the Monday
#: session was last saved 69 minutes before this message). Widened here to
#: see what a realistic set looks like; the right bound is a design question.
OPEN_RECENCY = timedelta(hours=12)
HORIZON = timedelta(days=7)

# ---------------------------------------------------------------------------
# The portable part: catalog + resolver shapes. No I/O except the model call.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Referent:
    """One standing thing, as the model may see it. Every field is minted by
    the system; nothing here came from the user's words."""

    ref_id: str  # host-minted, opaque to the model
    key: str  # delivery key (session key)
    agent: str
    kind: str
    day: date | None
    status: str
    last_activity: datetime
    accepts: tuple[str, ...]
    #: Whether the row was opened by autostart and never touched by the user.
    #: Held apart from `status` on purpose: run 4 varied a *phrase inside*
    #: `status` ("open" vs "open, opened automatically, never used") and that is
    #: not the same claim as a structured field. #352's door sees this row as
    #: the common case, so the shape of this datum is load-bearing there.
    never_used: bool = False
    #: A short, capped list of the plan's own block titles and times. Present so
    #: a message naming a block inside a plan can be resolved at all: without it
    #: the model is shown a day and a status and correctly answers `none`, which
    #: at a door that can create a session is how #275's duplicate is minted.
    gist: tuple[str, ...] = ()

    def describe(self, now: datetime, *, mode: str = "prose") -> dict:
        """``mode``: how the never-used fact is carried to the model.

        prose      -- a phrase inside `status` (what run 4 actually varied)
        structured -- a plain status plus a boolean field
        both       -- the boolean field AND a rendered sentence
        """
        ago = now - self.last_activity
        hours = round(ago.total_seconds() / 3600, 1)
        out = {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "day": (
                f"{self.day.isoformat()} ({self.day.strftime('%A')})"
                if self.day
                else "no day locked yet"
            ),
            "status": self.status if mode in ("structured", "gist") else self.prose_status,
            "last_activity": f"{hours}h ago",
            "accepts": list(self.accepts),
        }
        if mode in ("structured", "both", "gist"):
            out["opened_automatically_never_used"] = self.never_used
        if mode == "gist" and self.gist:
            out["plan_contains"] = list(self.gist[:12])
        return out

    @property
    def prose_status(self) -> str:
        """Run 4's arm: the fact carried as a phrase inside the status string."""
        return (
            "open, opened automatically, never used" if self.never_used else self.status
        )


def load_referents(db_path: str, *, owner: str, now: datetime) -> list[Referent]:
    """What `standing_for` would return, widened to every qualifying row.

    Same predicate family as the ledger: open and saved recently, or
    committed with a day inside the horizon. Cancelled never appears.
    """
    since = (now - OPEN_RECENCY).astimezone(timezone.utc).replace(tzinfo=None)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        """
        select session_key, status, planning_date, updated_at, revision
        from timeboxing_session_states
        where owner_user_id = ?
          and created_at < ?
          and (
            (status = 'open' and updated_at >= ?)
            or (status = 'committed' and planning_date between ? and ?)
          )
        order by updated_at desc
        """,
        (
            owner,
            NOW.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
            since.isoformat(sep=" "),
            now.date().isoformat(),
            (now + HORIZON).date().isoformat(),
        ),
    ).fetchall()
    conn.close()
    out: list[Referent] = []
    for i, (key, status, pdate, updated, revision) in enumerate(rows):
        last = datetime.fromisoformat(updated).replace(tzinfo=timezone.utc)
        # Run 1: an auto-opened, never-touched, day-less DM session drew
        # "plan tomorrow" 7/8 under shape C. Revision 1 is the opening turn
        # (session_start.UNTOUCHED_REVISION); say so instead of hiding it.
        untouched = status == "open" and revision <= 1
        out.append(
            Referent(
                ref_id=f"r{i + 1}",
                key=key,
                agent="timeboxing_agent",
                kind="timeboxing session (a plan for one day)",
                day=date.fromisoformat(pdate) if pdate else None,
                status=status,
                never_used=untouched,
                last_activity=last,
                accepts=(
                    ("revise the committed plan", "add a fact about the day")
                    if status == "committed"
                    else ("continue planning", "answer the open question", "cancel")
                ),
            )
        )
    return out


#: The ledger AS OF the message. Run 3 drew a different set than run 2 because a
#: peer committed the Monday plan mid-run and `describe` reads *current* status,
#: not status as of `now`. Any eval over live session rows measures the ledger's
#: drift unless it is frozen. Rows below are `timeboxing_session_states` at
#: 2026-09-05 13:51, verbatim.
FROZEN: tuple[tuple[str, str, str | None, str, int], ...] = (
    ("C0AA6HC1RJL:1788603379.318719", "open", "2026-09-07", "2026-09-05 10:42:53", 7),
    ("D09A0RE9P7G:dm", "open", None, "2026-09-05 01:43:00", 1),
    ("C0AA6HC1RJL:1788571682.407949", "committed", "2026-09-05", "2026-09-05 01:41:01", 7),
)


def frozen_referents(
    *, drop_untouched: bool, rows: tuple = (), now: datetime | None = None
) -> list["Referent"]:
    """The fixture, through the same descriptor code the live path uses.

    ``drop_untouched`` is the catalog-policy question run 3 raised: an
    auto-opened session nobody has touched, with no day locked, matched
    "add a dentist appointment on tuesday" 7/8. It is an empty room -- there is
    nothing about it a user could be referring to. Is it a referent at all?
    """
    out: list[Referent] = []
    for key, status, pdate, updated, revision in (rows or FROZEN):
        untouched = status == "open" and revision <= 1
        if untouched and drop_untouched:
            continue
        out.append(
            Referent(
                ref_id=f"r{len(out) + 1}",
                key=key,
                agent="timeboxing_agent",
                kind="timeboxing session (a plan for one day)",
                day=date.fromisoformat(pdate) if pdate else None,
                status=status,
                never_used=untouched,
                gist=AMBIG_GIST.get(key, ()),
                last_activity=datetime.fromisoformat(updated).replace(tzinfo=timezone.utc),
                accepts=(
                    ("revise the committed plan", "add a fact about the day")
                    if status == "committed"
                    else ("continue planning", "answer the open question", "cancel")
                ),
            )
        )
    return out


#: The #275 incident, from production rather than construction (supplied by
#: #352's owner). On 2026-09-03 two sessions for Hugo and Friday 2026-09-04 ran
#: in parallel in #plan-sessions; one committed over the other's morning blocks
#: (journal id 186). Two open sessions, one day, no structural tie-break: this
#: is what `ambiguous` exists for, and #352's door cannot be built correctly if
#: the resolver cannot return it here.
#:
#: Caveat: `updated_at` and `revision` are the rows' CURRENT values, not their
#: values at 12:15 -- the store keeps no history, which is the same as-of gap
#: this spike flagged elsewhere. It does not affect the question asked, since
#: both rows are same-day open sessions either way.
AMBIG_NOW = datetime(2026, 9, 3, 12, 15, tzinfo=TZ)
AMBIG: tuple[tuple[str, str, str | None, str, int], ...] = (
    ("C0AA6HC1RJL:1788429283.534419", "open", "2026-09-04", "2026-09-03 10:17:54", 10),
    ("C0AA6HC1RJL:1788429809.317849", "open", "2026-09-04", "2026-09-03 10:14:14", 8),
)

#: Every message here is about Friday, and Friday has two sessions. Only the
#: last is expected to resolve: nothing stands for Sunday.
#: Labels are ground truth given full knowledge of both plans, which is the
#: point: the no-gist arm cannot know it and should visibly fail the first three.
AMBIG_CASES: list[tuple[str, str]] = [
    ("move PR1 later", "s1"),  # PR1 is the first session's own handle
    ("move the finances block later", "s1"),  # only the first plan has Finances
    ("push the investor call prep later", "s2"),  # only the second has it
    ("move the gym to the morning", "ambiguous"),  # both plans have a gym
    ("cancel that session", "ambiguous"),
    ("make it start at 10", "ambiguous"),
    ("plan sunday", "none"),
    # False-positive probes (#352's owner, blind review): the gist added content,
    # and the risk content brings is a model inventing a match. Nothing below
    # exists in EITHER plan, so a gist arm that resolves one of these is worse
    # than no gist -- it would be confidently naming a session over a block that
    # is not in it. `none` is the only correct answer.
    ("move the dentist earlier", "none"),
    ("push the standup to 11", "none"),
    ("can you shorten the school run", "none"),
    ("move the physio appointment to friday morning", "none"),
]


#: What each incident session's plan actually holds, from its own artifacts.
#: The two plans differ almost entirely, which is what makes them a fair test:
#: a gist should resolve a message naming a block only one of them contains,
#: and must still answer ambiguous for a block both contain (both have a gym).
AMBIG_GIST: dict[str, tuple[str, ...]] = {
    "C0AA6HC1RJL:1788429283.534419": (
        "PR1 Serious C2F work 10:30-12:00",
        "EVT2 Kapper 12:00-12:30 (foreign, fixed)",
        "LN1 Lunch 12:30-13:00",
        "DW1 Validate agent demos 13:00-13:45",
        "INV1 Finances 13:45-14:30",
        "OAT1 Oats 16:00-16:15",
        "GYM1 Gym (chest) 18:00-19:00",
        "DIN1 Dinner 19:15-20:00",
        "SHD1 Evening shutdown ritual 20:00-21:00",
    ),
    "C0AA6HC1RJL:1788429809.317849": (
        "PR review - stage-UX, ends 11:30",
        "Kapper 12:00-12:30 (calendar event, foreign)",
        "Lunch ~12:30",
        "Deep work - constraint memory design, 90 minutes",
        "Prepare the Monday investor call 15:00-16:00",
        "Oats 16:00",
        "Gym 18:00 (chest)",
        "Dinner ~19:30",
        "Evening shutdown ritual",
        "Sleep 23:00",
    ),
}


class Judge:
    def __init__(self) -> None:
        self.base = os.environ["OPENROUTER_BASE_URL"].rstrip("/")
        self.key = os.environ["OPENROUTER_API_KEY"]
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        self.calls = 0
        self.latency: list[float] = []

    async def ask(self, system: str, user: str) -> dict:
        t0 = time.perf_counter()
        for attempt in range(3):
            try:
                r = await self.client.post(
                    f"{self.base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "reasoning": {"effort": "minimal"},
                        "response_format": {"type": "json_object"},
                    },
                )
                r.raise_for_status()
                body = r.json()
                if "choices" not in body:
                    raise RuntimeError(body.get("error", body))
                self.calls += 1
                self.latency.append(time.perf_counter() - t0)
                return json.loads(body["choices"][0]["message"]["content"])
            except (httpx.TimeoutException, RuntimeError, json.JSONDecodeError) as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
        raise AssertionError


SHARED_PREAMBLE = """You route one message a user just typed to an assistant.
The user has some *standing things*: conversations or plans that already exist and can be continued.
Decide whether the message is about one of them, or is a new request that concerns none of them.
Judge by meaning. A message that continues, changes, questions, or cancels a standing thing is about it.
A message that asks for something none of the standing things covers is about none of them.
Never invent identifiers. Return only JSON."""

SHAPE_A_PROMPT = SHARED_PREAMBLE + """
Answer with {"decision": "<ref_id>" | "none" | "ambiguous", "why": "<one short sentence>"}.
Use "ambiguous" only when the message is about a standing thing but you cannot tell which of two or more."""

SHARP_PROMPT = """You route one message a user just typed to a scheduling assistant.
The user has some *standing things*: plans for a particular day that already exist and can be continued.
Decide which standing thing, if any, this message is about.

A message is about a standing thing when it continues, changes, questions, or ends THAT day's plan --
including a question about what that plan says.
A message is about NONE of them when it asks for something new that no listed plan covers: a fact,
an errand, a reminder, or planning a day that is not listed. Wanting something scheduled is not the
same as continuing an existing plan for a day.
Choose "ambiguous" only when the message is clearly about one of the listed plans but two or more fit equally.

Judge by meaning. Never invent identifiers. Return only JSON.
Answer with {"decision": "<ref_id>" | "none" | "ambiguous", "why": "<one short sentence>"}."""

SHAPE_C_PROMPT = SHARED_PREAMBLE + """
You are shown exactly ONE standing thing. Answer whether the message is about it.
{"about_this": true | false, "why": "<one short sentence>"}"""


async def resolve_shape_a(
    judge: Judge,
    refs: list[Referent],
    text: str,
    now: datetime,
    prompt: str = SHAPE_A_PROMPT,
    mode: str = "prose",
) -> str:
    payload = {
        "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
        "standing_things": [r.describe(now, mode=mode) for r in refs],
        "message": text,
    }
    answer = await judge.ask(prompt, json.dumps(payload, ensure_ascii=False))
    decision = str(answer.get("decision", ""))
    valid = {r.ref_id for r in refs} | {"none", "ambiguous"}
    return decision if decision in valid else f"INVALID({decision})"


async def resolve_shape_c(judge: Judge, refs: list[Referent], text: str, now: datetime) -> str:
    async def one(r: Referent) -> bool:
        payload = {
            "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
            "standing_thing": r.describe(now, mode="prose"),
            "message": text,
        }
        answer = await judge.ask(SHAPE_C_PROMPT, json.dumps(payload, ensure_ascii=False))
        return bool(answer.get("about_this") is True)

    votes = await asyncio.gather(*(one(r) for r in refs))
    yes = [r.ref_id for r, v in zip(refs, votes) if v]
    if len(yes) == 1:
        return yes[0]
    return "none" if not yes else "ambiguous"


# ---------------------------------------------------------------------------
# The throwaway shell: cases, resampling, report.
# ---------------------------------------------------------------------------

#: (message, expected). Expected names a day (mapped to a ref_id at runtime),
#: or "none" / "ambiguous". Labels are my expectation of the right routing,
#: to be argued with.
CASES: list[tuple[str, str]] = [
    ("can you replan today so the gym is before dinner?", "2026-09-05"),
    ("plan saturday", "2026-09-05"),  # #275: a second opening for a planned day
    ("let's plan monday", "2026-09-07"),  # #275: a second opening for an open day
    ("actually make monday start at 10", "2026-09-07"),
    ("I'll wake up at 11 on monday", "2026-09-07"),
    ("move the gym to the morning", "ambiguous"),  # two days, no day named
    ("cancel that session", "ambiguous"),
    ("plan tomorrow", "none"),  # Sunday: nothing stands
    ("what's the weather tomorrow", "none"),
    ("add a dentist appointment on tuesday", "none"),
    ("remind me to pay taxes", "none"),
    ("what did we decide about dinner?", "2026-09-05"),
    ("I want to finish the finance ticket in the first shallow block", "2026-09-05"),
    ("is it planned?", "none"),  # a planning card resolves this structurally; here nothing does
]


#: The ambiguity fixture's two rows, in fixture order, so a label can name one.
_AMBIG_ORDER = {"s1": 0, "s2": 1}


def expected_id(expected: str, refs: list[Referent]) -> str:
    if expected in _AMBIG_ORDER:
        i = _AMBIG_ORDER[expected]
        return refs[i].ref_id if i < len(refs) else f"MISSING({expected})"
    if expected in ("none", "ambiguous"):
        return expected
    # Prefer the committed session for a day: it is the plan that stands.
    for r in sorted(refs, key=lambda r: r.status != "committed"):
        if r.day and r.day.isoformat() == expected:
            return r.ref_id
    return f"MISSING({expected})"


async def run(draws: int, say: str | None, scenario: str = "incident") -> None:
    """Four arms on one frozen fixture, so only one thing varies at a time.

    Shape C is not run any more: runs 2 and 3 both retired it (a candidate shown
    alone has no contrast, so the model affirms nearly everything). What is left
    to settle is the catalog policy and the wording, on the pin.
    """
    # Run 5 holds the prompt at the winner and varies ONLY how the never-used
    # fact is carried, which is what run 4 failed to isolate. The fourth arm is
    # #352's fallback: a structured field the provider renders into a sentence,
    # so the store keeps a real field and the model still reads prose.
    _moment_hdr = AMBIG_NOW if scenario == "ambiguity" else NOW
    arms = (
        [
            ("no gist: day + status only", SHARP_PROMPT, False, "structured"),
            ("gist: + block titles and times", SHARP_PROMPT, False, "gist"),
        ]
        if scenario == "ambiguity"
        else [
            ("prose status (run 4's arm3)", SHARP_PROMPT, False, "prose"),
            ("structured boolean field", SHARP_PROMPT, False, "structured"),
            ("structured + rendered sentence", SHARP_PROMPT, False, "both"),
            ("never-used row dropped", SHARP_PROMPT, True, "prose"),
        ]
    )
    print(f"\x1b[1mmodel\x1b[0m {MODEL}   \x1b[1mscenario\x1b[0m {scenario}   \x1b[1mnow\x1b[0m {_moment_hdr:%Y-%m-%d %H:%M %A}   \x1b[1mdraws\x1b[0m {draws}   \x1b[2m(frozen fixture)\x1b[0m")
    _rows = AMBIG if scenario == "ambiguity" else FROZEN
    _moment = AMBIG_NOW if scenario == "ambiguity" else NOW
    for label, _, drop, _mode in arms[:1]:
        refs = frozen_referents(drop_untouched=drop, rows=_rows, now=_moment)
        print(f"  \x1b[1m{'without' if drop else 'with'} the never-used session\x1b[0m: " + " | ".join(
            f"{r.ref_id} {r.day or 'no day'} {r.status.split(',')[0]}" for r in refs))
    print()

    judge = Judge()
    if scenario == "ambiguity":
        cases, rows, moment = AMBIG_CASES, AMBIG, AMBIG_NOW
    else:
        cases, rows, moment = CASES, FROZEN, NOW
    if say is not None:
        cases = [(say, "?")]

    async def one_arm(prompt: str, drop: bool, mode: str):
        refs = frozen_referents(drop_untouched=drop, rows=rows, now=moment)
        out = []
        for text, expected in cases:
            exp = expected_id(expected, refs) if expected != "?" else "?"
            got = list(await asyncio.gather(
                *(resolve_shape_a(judge, refs, text, moment, prompt, mode) for _ in range(draws))
            ))
            out.append((text, exp, got))
        return out

    results = await asyncio.gather(*(one_arm(pr, dr, md) for _, pr, dr, md in arms))

    def cell(got: list[str], exp: str) -> str:
        hit = sum(1 for g in got if g == exp)
        colour = "\x1b[32m" if hit == draws else ("\x1b[33m" if hit >= draws * 0.75 else "\x1b[31m")
        top = max(set(got), key=got.count)
        extra = "" if hit == draws else f" \x1b[2m{top}:{got.count(top)}\x1b[0m"
        return f"{colour}{hit}/{draws}\x1b[0m{extra}"

    header = "message".ljust(46) + "expect".ljust(9) + "".join(
        f"arm{i + 1}".ljust(18) for i in range(len(arms)))
    print("\x1b[1m" + header + "\x1b[0m")
    for row, (text, exp, _) in enumerate(results[0]):
        line = text[:44].ljust(46) + exp.ljust(9)
        for arm in results:
            _, e, got = arm[row]
            line += cell(got, e).ljust(30)
        print(line)

    print()
    for i, (label, _, _, _mode) in enumerate(arms):
        total = sum(sum(1 for g in got if g == exp) for _, exp, got in results[i])
        possible = len(results[i]) * draws
        clean = sum(1 for _, exp, got in results[i] if all(g == exp for g in got))
        print(f"  \x1b[1marm{i + 1}\x1b[0m {label:<38} {total}/{possible} draws   {clean}/{len(results[i])} cases unanimous")

    lat = sorted(judge.latency)
    print(f"\n\x1b[2m{judge.calls} calls, latency p50 {lat[len(lat) // 2]:.2f}s p90 {lat[int(len(lat) * 0.9)]:.2f}s\x1b[0m")
    await judge.client.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=8)
    ap.add_argument("--say", type=str, default=None)
    ap.add_argument("--scenario", choices=("incident", "ambiguity"), default="incident")
    ns = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY missing (.env)")
    asyncio.run(run(ns.draws, ns.say, ns.scenario))

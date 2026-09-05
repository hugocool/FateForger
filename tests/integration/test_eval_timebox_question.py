# tests/integration/test_eval_timebox_question.py
"""Quality of the timeboxing surface's question decision against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt.
Every case resamples -- one draw tests the model's luck -- and the rate is the
assertion. No temperature pin.

The break-it check strips `QUESTION_PARAGRAPH` and expects the discrimination
to collapse. It is aimed at a question that *carries a fact*, not at a plain
interrogative, because measurement said so: with the paragraph gone, "is it
planned?" and "what did we settle on for lunch?" still answer `question` at
7/8 and 8/8 -- the label in `allowed_decisions` carries those on its own, so
asserting on them tested the label. "did you move lunch? I sleep 00:30-08:30"
answers with the fact 8/8 with the paragraph and 1/8 without. That is the
clause doing the work, so that is what the check strips.

A draw that raises reached no decision and is retried once; the retry count is
reported, never asserted (#325).
"""

from __future__ import annotations

import asyncio
import os
import traceback
from collections import Counter
from datetime import date

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

SAMPLES = 8
THRESHOLD = 7


def _report(results: list, retries: int = 0) -> str:
    lines = [f"{retries} draw(s) retried after reaching no decision"]
    for r in results:
        if isinstance(r, BaseException):
            lines.append("".join(traceback.format_exception(r)).rstrip())
        else:
            lines.append(repr(r))
    return "\n---\n".join(lines)


def _fresh():
    from fateforger.agents.timeboxing.session_contracts import PlanningSessionSnapshot

    return PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")


def _committed():
    from fateforger.agents.timeboxing.session_contracts import (
        ArtifactKind,
        PlanningArtifact,
        PlanningDay,
        PlanningSessionSnapshot,
    )

    # The receipt is what the committed state hands the binder as its pending
    # artifact, so `revise` has something to name. Payload keys are the ones
    # `timeboxing_host` actually writes -- a made-up shape here would prove
    # the model reads a receipt this system never mints.
    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={
            "committed": True,
            "tx_id": "tx_eval",
            "reason": None,
            "candidate_digest": "d" * 64,
            "calendar_backend": "google",
            "durable": True,
        },
        dependency_revisions={"validated_candidate": 1},
    )
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=9,
        owner_user_id="U1",
        status="committed",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1
        ),
        artifacts=[receipt],
    )


async def _intents(text: str, snapshot) -> tuple[list, int]:
    """`SAMPLES` concurrent draws, and how many of them had to be retried.

    A draw that raises is a draw that reached *no* decision, never a draw that
    reached the wrong one: the interpreter returns a typed intent for every
    reading the model produces, and only raises when the endpoint gave it
    nothing to read. Folding that into the rate reads a broken call as a
    misjudged one -- which is exactly how this eval's first run made a working
    paragraph look like a prompt bug. So each draw is retried once, and the
    retry count travels back with the results to be reported. It is not
    asserted on: the endpoint's error rate is #325's problem, not this eval's.
    """

    from fateforger.llm.factory import build_autogen_chat_client
    from fateforger.slack_bot.timeboxing_intents import TimeboxingIntentInterpreter

    # The client production builds for this interpreter, agent type and all:
    # `core.runtime._build_timeboxing_intent_interpreter` asks for exactly this
    # one. Naming a model here instead would measure a model nothing runs on --
    # the `.env` pins decide, and this eval reports whichever they name.
    interpreter = TimeboxingIntentInterpreter(
        build_autogen_chat_client("timeboxing_agent")
    )
    retries = 0

    async def one():
        nonlocal retries
        try:
            return await interpreter.interpret(text, snapshot)
        except Exception:  # noqa: BLE001 -- any exception is "no decision"
            retries += 1
            # Exactly once. A second failure is the run's answer, and it
            # reaches `_count` as the miss it is rather than being retried
            # until the endpoint happens to agree.
            return await interpreter.interpret(text, snapshot)

    results = await asyncio.gather(
        *(one() for _ in range(SAMPLES)), return_exceptions=True
    )
    return results, retries


def _outcome(result: object) -> str:
    """One word for what a draw produced.

    The decision it reached, or -- when it reached none -- the failure that
    stopped it. `SurfaceIntentError` is the wrapper the seam raises over
    anything the transport did, so the cause is the part worth naming: a draw
    that never got an answer out of the endpoint is not the same finding as a
    draw that answered the wrong thing, and a rate that prints them as one
    number reads a broken call as a misjudged one.
    """
    if isinstance(result, BaseException):
        cause = result.__cause__ or result
        return type(cause).__name__
    return type(result).__name__


def _count(results: list, kind: type, case: str = "", retries: int = 0) -> int:
    """How many of the draws landed on `kind` -- and say it out loud.

    The rate is the assertion, so the rate is what a run has to report, the
    cases that cleared the bar by one included: a 7/8 and an 8/8 are the same
    green and very different evidence. The breakdown and the retry count ride
    along so the misses can be read and the endpoint's flakiness stays visible
    without the rate absorbing it. All of it needs `-s`.
    """
    count = sum(
        1 for r in results if not isinstance(r, BaseException) and isinstance(r, kind)
    )
    breakdown = dict(Counter(_outcome(r) for r in results))
    print(
        f"[eval] {kind.__name__} {count}/{SAMPLES} <- {case!r} "
        f":: {breakdown} retries={retries}"
    )
    return count


# "did you put the gym in?", not the paragraph's own "did you add the gym?":
# a case whose exact words are in the prompt measures recall of the prompt.
QUESTIONS_FRESH = ["Is it planned?", "did you put the gym in?", "what's on my calendar tomorrow?", "is there a planning session today?"]
STARTS = ["plan tomorrow", "let's timebox saturday", "start", "ok let's go"]
CANCELS = ["cancel this", "never mind, not today"]
QUESTIONS_COMMITTED = ["what did we settle on for lunch?", "when is deep work?"]
# A reply that asks *and* supplies a fact is the fact -- the clause the plain
# interrogatives never exercise, and the one the paragraph actually carries.
# These two are the positive half of the break-it check below; the same texts
# appear there with the paragraph stripped.
MIXED_COMMITTED = [
    "did you move lunch? I sleep 00:30-08:30",
    "is deep work still at 9? also I get up at 07:00",
]
FACTS_COMMITTED = ["I sleep 00:30–08:30", *MIXED_COMMITTED]
REVISIONS_COMMITTED = ["move the work two hours later"]


@pytest.mark.parametrize("text", QUESTIONS_FRESH)
async def test_a_question_before_a_day_is_asked(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion

    results, retries = await _intents(text, _fresh())
    assert _count(results, AskQuestion, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", STARTS)
async def test_a_start_before_a_day_starts(text):
    from fateforger.agents.timeboxing.session_contracts import StartSession

    results, retries = await _intents(text, _fresh())
    assert _count(results, StartSession, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", CANCELS)
async def test_a_cancel_before_a_day_cancels(text):
    from fateforger.agents.timeboxing.session_contracts import CancelSession

    results, retries = await _intents(text, _fresh())
    assert _count(results, CancelSession, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", QUESTIONS_COMMITTED)
async def test_a_question_after_commit_is_asked_not_revised(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion

    results, retries = await _intents(text, _committed())
    assert _count(results, AskQuestion, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", FACTS_COMMITTED)
async def test_a_fact_after_commit_is_still_a_fact(text):
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    results, retries = await _intents(text, _committed())
    assert _count(results, ProvidePlanningFacts, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", REVISIONS_COMMITTED)
async def test_a_revision_after_commit_is_still_a_revision(text):
    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    results, retries = await _intents(text, _committed())
    assert _count(results, ReviseArtifact, text, retries) >= THRESHOLD, _report(results, retries)


@pytest.mark.parametrize("text", MIXED_COMMITTED)
async def test_break_it_without_the_question_paragraph_the_fact_is_lost_to_the_question(
    text, monkeypatch
):
    """Strip the paragraph and a question carrying a fact stops being the fact.

    The break-it check used to strip the paragraph and expect plain
    interrogatives -- "is it planned?", "what did we settle on for lunch?" --
    to stop reading as questions. Measured on the pro pin 2026-09-05, they do
    not: they answer `question` at 7/8 and 8/8 with the paragraph gone, because
    `question` is already in `allowed_decisions` and `GENERIC_PREAMBLE` says to
    choose from that list. The label alone carries a pure interrogative, so
    that assertion was measuring the label, not the paragraph.

    What the paragraph carries is the harder half: *"A reply that asks and also
    supplies a fact is that fact: the fact changes the day and the question
    does not."* Strip it and the fact is dropped -- the user gets an answer to
    their question while the sleep boundary they just stated goes nowhere,
    which is the silent-wrong-answer shape the whole ban exists to stop. The
    same two texts assert the positive above, in
    `test_a_fact_after_commit_is_still_a_fact`.
    """
    import fateforger.slack_bot.timeboxing_intents as intents
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    monkeypatch.setattr(
        intents, "_TIMEBOX_PROMPT_FRAGMENT", intents._TIMEBOX_PROMPT_FRAGMENT_BASE
    )
    results, retries = await _intents(text, _committed())
    assert _count(results, ProvidePlanningFacts, text, retries) < THRESHOLD, _report(
        results, retries
    )

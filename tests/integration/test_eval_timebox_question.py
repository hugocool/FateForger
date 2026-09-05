# tests/integration/test_eval_timebox_question.py
"""Quality of the timeboxing surface's question decision against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt.
Every case resamples -- one draw tests the model's luck -- and the rate is the
assertion. No temperature pin.

Run it with `-s`; that is what makes the per-case counts visible:

    set -a; source .env; set +a
    PYTHONPATH=src ../../.venv/bin/python -m pytest \
        tests/integration/test_eval_timebox_question.py -m slow -q -s \
        -p no:cacheprovider

Two break-it families strip `QUESTION_PARAGRAPH`, because the paragraph does
two separable jobs and a plain interrogative exercises neither. With the
paragraph gone, "is it planned?" and "what did we settle on for lunch?" still
answer `question` at 7/8 and 8/8 -- `question` is already in
`allowed_decisions` and `GENERIC_PREAMBLE` says to choose from that list, so
asserting on those measured the label. What does move, measured 2026-09-05:

* on a fresh session, "what's on my calendar tomorrow?" answers `start` at
  6/8, 6/8 and 7/8 across three stripped runs, against `question` 8/8 with
  the paragraph and `start` never once -- asked becomes started, which is the
  regression this branch is named for;
* on a committed session, "did you move lunch? I sleep 00:30-08:30" answers
  with the fact 8/8 with the paragraph and 1/8 without -- the clause about a
  reply that asks *and* supplies one.

Both assert the *flip* -- the wrong decision outnumbering the right one --
and not the absence of the right one. An absence-based bar is cleared by two
lost calls, which is how the first version of this check "passed" while the
paragraph was doing nothing at all.

A draw whose exception carries a transport cause reached no decision and is
retried once; the retry count is reported, never asserted (#325). A draw that
reached a *wrong* decision is never retried -- see `_is_transport`.
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


def _is_transport(exc: BaseException) -> bool:
    """Whether a raise means "the endpoint gave us nothing", not "wrong answer".

    Exactly one raise site on the interpreter's path wraps a cause --
    `surface_intents.py`'s ``except Exception as exc: raise SurfaceIntentError(
    ...) from exc``, which is the transport and JSON-parse layer. Every other
    raise is a judgement the model actually made and lost, and none of them
    sets ``__cause__``:

    * `SurfaceIntentError` for a decision outside `allowed_decisions`
      (`surface_intents.py:205`), raised after the call returned;
    * `ValidationError` for output that does not fit the narrowed schema
      (`surface_intents.py:193`), re-raised as itself;
    * `SurfaceIntentError` for a response carrying no string content
      (`surface_intents.py:191`);
    * the binder's own `ValueError`s -- `provide_facts` with no facts,
      `revise` with no instruction, a `steer_not_today` naming a row that is
      not on the card (`timeboxing_intents.py:559,602,606,615`).

    Retrying any of those would be the eval re-rolling until the run agreed
    with it. The first is the exact degenerate answer a stripped paragraph is
    *supposed* to produce, so swallowing it would hide the break-it result
    this file exists to measure.
    """

    from fateforger.slack_bot.surface_intents import SurfaceIntentError

    return isinstance(exc, SurfaceIntentError) and exc.__cause__ is not None


async def _intents(text: str, snapshot) -> tuple[list, int]:
    """`SAMPLES` concurrent draws, and how many of them had to be retried.

    A draw lost to the transport reached *no* decision, and folding that into
    the rate reads a broken call as a misjudged one -- which is exactly how
    this eval's first run made a working paragraph look like a prompt bug. So
    a draw that failed in transport is redrawn once, and the retry count
    travels back with the results to be reported. It is not asserted on: the
    endpoint's error rate is #325's problem, not this eval's.

    A draw that reached a wrong decision is *not* redrawn. `_is_transport`
    draws that line, and it is drawn narrowly on purpose: a blind
    ``except Exception`` here would retry a disallowed decision, which is the
    one outcome the break-it families are trying to observe.
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
        except Exception as exc:
            if not _is_transport(exc):
                # A decision was reached and it was the wrong one. That is the
                # measurement, not an error to paper over.
                raise
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


# Not one of these texts appears inside `QUESTION_PARAGRAPH`. A case whose
# exact words are quoted in the prompt measures recall of the prompt rather
# than the judgement the prompt is meant to produce, so every text the
# paragraph quotes -- "is it planned?", "did you add the gym?", "what did we
# settle on for lunch?", "when is deep work?", "plan tomorrow", "start", "ok
# let's go" -- is reworded here to the same intent in different words.

#: Named once because the fresh-session break-it family strips the paragraph
#: from this exact text, and the two halves have to be provably the same one.
CALENDAR_QUESTION = "what's on my calendar tomorrow?"

QUESTIONS_FRESH = [
    "has it been scheduled?",
    "did you put the gym in?",
    CALENDAR_QUESTION,
    "is there a planning session today?",
]
STARTS = [
    "plan my day tomorrow",
    "let's timebox saturday",
    "kick it off",
    "right, let's begin",
]
CANCELS = ["cancel this", "never mind, not today"]
QUESTIONS_COMMITTED = [
    "what did we decide about lunch?",
    "when's the deep-work block?",
]
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
#: The fresh-session half of the break-it check. One text, and it is the same
#: object `QUESTIONS_FRESH` asserts the positive on.
BREAK_IT_FRESH = [CALENDAR_QUESTION]


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


@pytest.mark.parametrize("text", BREAK_IT_FRESH)
async def test_break_it_without_the_question_paragraph_a_question_starts_a_session(
    text, monkeypatch
):
    """Strip the paragraph and a question about the day *starts* the day.

    This is the regression the branch is named for, reproduced on demand.
    "what's on my calendar tomorrow?" names a day, and with nothing in the
    prompt to say that asking about a day is not asking for one, the model
    reads it as `start`: 6/8, 6/8 and 7/8 to `StartSession` across three
    stripped runs on 2026-09-05, against `AskQuestion` 8/8 with the paragraph
    and `StartSession` not once in any unstripped run. In production that is a
    planning session opened by somebody who only wanted to know what was on.

    Asserted as a flip rather than a bar. `StartSession >= THRESHOLD` would
    have failed two of those three runs at 6/8 while the paragraph was plainly
    doing its job; and the mirror-image bar, `AskQuestion < THRESHOLD` alone,
    is cleared by two lost calls with the paragraph doing nothing. Only the
    two together say the decision moved: a transport failure subtracts from
    both counts and can never manufacture `started > asked`.
    """
    import fateforger.slack_bot.timeboxing_intents as intents
    from fateforger.agents.timeboxing.session_contracts import (
        AskQuestion,
        StartSession,
    )

    monkeypatch.setattr(
        intents, "_TIMEBOX_PROMPT_FRAGMENT", intents._TIMEBOX_PROMPT_FRAGMENT_BASE
    )
    results, retries = await _intents(text, _fresh())
    asked = _count(results, AskQuestion, text, retries)
    started = _count(results, StartSession, text, retries)
    assert asked < THRESHOLD, _report(results, retries)
    assert started > asked, _report(results, retries)


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

    The flip, not the absence, for the reason given on the fresh-session check:
    `ProvidePlanningFacts < THRESHOLD` on its own is satisfied by two lost
    draws. `asked > kept` is not.
    """
    import fateforger.slack_bot.timeboxing_intents as intents
    from fateforger.agents.timeboxing.session_contracts import (
        AskQuestion,
        ProvidePlanningFacts,
    )

    monkeypatch.setattr(
        intents, "_TIMEBOX_PROMPT_FRAGMENT", intents._TIMEBOX_PROMPT_FRAGMENT_BASE
    )
    results, retries = await _intents(text, _committed())
    kept = _count(results, ProvidePlanningFacts, text, retries)
    asked = _count(results, AskQuestion, text, retries)
    assert kept < THRESHOLD, _report(results, retries)
    assert asked > kept, _report(results, retries)

# tests/integration/test_eval_timebox_question.py
"""Quality of the timeboxing surface's question decision against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt.
Every case resamples -- one draw tests the model's luck -- and the rate is the
assertion. No temperature pin. The break-it check strips the discriminating
paragraph and expects the questions to stop being read as questions: a
discriminator that passes without its discriminating sentence is not one.
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


def _report(results: list) -> str:
    lines = []
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


async def _intents(text: str, snapshot) -> list:
    from fateforger.llm.factory import build_autogen_chat_client
    from fateforger.slack_bot.timeboxing_intents import TimeboxingIntentInterpreter

    # The client production builds for this interpreter, agent type and all:
    # `core.runtime._build_timeboxing_intent_interpreter` asks for exactly this
    # one. Naming a model here instead would measure a model nothing runs on --
    # the `.env` pins decide, and this eval reports whichever they name.
    interpreter = TimeboxingIntentInterpreter(
        build_autogen_chat_client("timeboxing_agent")
    )

    async def one():
        return await interpreter.interpret(text, snapshot)

    return await asyncio.gather(*(one() for _ in range(SAMPLES)), return_exceptions=True)


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


def _count(results: list, kind: type, case: str = "") -> int:
    """How many of the draws landed on `kind` -- and say it out loud.

    The rate is the assertion, so the rate is what a run has to report, the
    cases that cleared the bar by one included: a 7/8 and an 8/8 are the same
    green and very different evidence. The breakdown rides along so the misses
    can be read. Both need `-s`.
    """
    count = sum(
        1 for r in results if not isinstance(r, BaseException) and isinstance(r, kind)
    )
    breakdown = dict(Counter(_outcome(r) for r in results))
    print(f"[eval] {kind.__name__} {count}/{SAMPLES} <- {case!r} :: {breakdown}")
    return count


QUESTIONS_FRESH = ["Is it planned?", "did you add the gym?", "what's on my calendar tomorrow?", "is there a planning session today?"]
STARTS = ["plan tomorrow", "let's timebox saturday", "start", "ok let's go"]
CANCELS = ["cancel this", "never mind, not today"]
QUESTIONS_COMMITTED = ["what did we settle on for lunch?", "when is deep work?"]
FACTS_COMMITTED = ["I sleep 00:30–08:30"]
REVISIONS_COMMITTED = ["move the work two hours later"]


@pytest.mark.parametrize("text", QUESTIONS_FRESH)
async def test_a_question_before_a_day_is_asked(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion

    results = await _intents(text, _fresh())
    assert _count(results, AskQuestion, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", STARTS)
async def test_a_start_before_a_day_starts(text):
    from fateforger.agents.timeboxing.session_contracts import StartSession

    results = await _intents(text, _fresh())
    assert _count(results, StartSession, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", CANCELS)
async def test_a_cancel_before_a_day_cancels(text):
    from fateforger.agents.timeboxing.session_contracts import CancelSession

    results = await _intents(text, _fresh())
    assert _count(results, CancelSession, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", QUESTIONS_COMMITTED)
async def test_a_question_after_commit_is_asked_not_revised(text):
    from fateforger.agents.timeboxing.session_contracts import AskQuestion

    results = await _intents(text, _committed())
    assert _count(results, AskQuestion, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", FACTS_COMMITTED)
async def test_a_fact_after_commit_is_still_a_fact(text):
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    results = await _intents(text, _committed())
    assert _count(results, ProvidePlanningFacts, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", REVISIONS_COMMITTED)
async def test_a_revision_after_commit_is_still_a_revision(text):
    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    results = await _intents(text, _committed())
    assert _count(results, ReviseArtifact, text) >= THRESHOLD, _report(results)


@pytest.mark.parametrize("text", QUESTIONS_FRESH[:2] + QUESTIONS_COMMITTED[:1])
async def test_break_it_without_the_question_paragraph_questions_are_not_questions(text, monkeypatch):
    """The paragraph is load-bearing. Strip it and the model has only a label."""
    import fateforger.slack_bot.timeboxing_intents as intents
    from fateforger.agents.timeboxing.session_contracts import AskQuestion

    monkeypatch.setattr(intents, "_TIMEBOX_PROMPT_FRAGMENT", intents._TIMEBOX_PROMPT_FRAGMENT_BASE)
    snapshot = _fresh() if text in QUESTIONS_FRESH else _committed()
    results = await _intents(text, snapshot)
    assert _count(results, AskQuestion, text) < THRESHOLD, _report(results)

"""A candidate the planner never applied must fail on the turn it happens.

Three consecutive real sessions produced three different candidate shapes:

    committed  blocks, day,  day_type, digest, patch, rendered, snapshot, timezone
    open       blocks,       digest, patch, rendered, snapshot
    refused    blocks, date, day_type

Only the first two carry a commit basis. The third was stored, rendered,
offered for approval, approved -- and refused three turns later as
`malformed_input`, because plan_commit went out as `plan_commit({}, {})`.

The host is right to refuse to invent the basis (`_with_commit_basis` says so:
inventing an empty one "would turn a missing patch into a forged one"). What
was missing is that a draft with nothing captured was stored anyway, so the
fault surfaced at commit time instead of at the turn that caused it.
"""

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    CandidateNotApplied,
    PlanningResult,
)
from fateforger.slack_bot.deepseek_timebox_planner import _with_commit_basis


def _result(kind: ArtifactKind) -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(kind=kind, payload={"blocks": [], "date": "2026-08-31"})
        ]
    )


def test_a_candidate_with_no_captured_patch_is_refused() -> None:
    """The exact shape of the session that was approved and could not commit."""

    with pytest.raises(CandidateNotApplied):
        _with_commit_basis(_result(ArtifactKind.VALIDATED_CANDIDATE), None)


def test_the_refusal_names_the_step_that_was_skipped() -> None:
    """A diagnostic nobody can act on is the bug that keeps happening here."""

    with pytest.raises(CandidateNotApplied) as caught:
        _with_commit_basis(_result(ArtifactKind.VALIDATED_CANDIDATE), None)
    assert "plan_apply" in str(caught.value)


def test_a_skeleton_needs_no_patch() -> None:
    """Only a candidate is committed, so only a candidate needs a basis."""

    result = _result(ArtifactKind.SKELETON)
    assert _with_commit_basis(result, None) is result


async def test_the_refusal_survives_the_planner_wrapper() -> None:
    """The layer that erased it in production.

    Asserting on `_with_commit_basis` alone missed this: `produce` wraps the
    runner in a broad `except Exception` that turned CandidateNotApplied into
    DependencyUnavailable, so the kernel's typed branch never ran and the user
    got "temporarily unavailable" for something deterministic. Caught only by
    driving a real session -- hence this test, which goes through `produce`.
    """

    from fateforger.slack_bot.deepseek_timebox_planner import DeepSeekTimeboxPlanner

    from .test_deepseek_timebox_planner import _input_brief

    class Tmbx:
        async def read(self, *a, **k):
            return {"ok": True}

    class Reader:
        async def query_constraints(self, **k):
            return []

    class Runner:
        async def run(self, brief, progress):
            raise CandidateNotApplied("call plan_apply first")

    planner = DeepSeekTimeboxPlanner.__new__(DeepSeekTimeboxPlanner)
    planner._tmbx_client = Tmbx()
    planner._calendar_id = "primary"
    planner._constraint_reader = Reader()
    planner._harness_runner = Runner()
    planner._clock = lambda: None

    with pytest.raises(CandidateNotApplied):
        await planner.produce(_input_brief(), None)

# tests/unit/test_patch_order_is_preserved.py
"""The order of `patch.ops` is the plan, and nothing on the path may touch it.

An add with no `after` follows the add listed before it, so the sequence of
the day is the sequence of the ops (`tmbx.core.ops`, and
`docs/superpowers/research/2026-08-30-patch-order-spike.md` for why). That
made a list's order load-bearing on a path where it never was: a patch
leaves the model as a tool call and reaches `apply_ops` through digests, a
hook file, a session envelope, a planning-result document and a journal row.

Every one of those was audited by hand once, and a hand audit expires the
moment somebody adds a `sorted()`. This file is that audit, run on every
commit. It **drives the real functions** rather than reading their source —
a test that searched for the string `sorted(` would pass forever and prove
nothing about what the code does, and the reorder that matters is the one
nobody wrote down as a sort.

The handles are the load-bearing fixture. `ZL1, MG1, BW1` is the order the
day happens in and the exact reverse of alphabetical order, so no sort
anywhere can produce the expected answer by luck. They are Hugo's real case:
Lunch, Gym, Walk, listed in that order and delivered as Walk, Gym, Lunch by
the handle-sorting tie-break this rule replaced.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, date, datetime

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    ArtifactSnapshot,
    PlanningArtifact,
    PlanningBrief,
    PlanningDay,
    PlanningResult,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.harness_bridge import _canonical_brief
from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    submit_planning_result,
)
from fateforger.slack_bot.timeboxing_session_store import (
    SqlAlchemyTimeboxingSessionRepository,
    _StoredSessionEnvelope,
)
from fateforger.slack_bot.validated_timebox_draft import (
    _candidate_digest as _dsh_candidate_digest,
)
from fateforger.slack_bot.validated_timebox_draft import (
    read_validated_candidate,
    record_validation_result,
)
from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.core.models import Plan
from tmbx.core.ops import Patch, apply_ops
from tmbx.journal.instrument import _ops_json
from tmbx.journal.store import JournalStore, init_journal
from tmbx.server import _candidate_digest as _tmbx_candidate_digest
from tmbx.server import build_server
from tmbx.service import PlanService

DAY = "2026-08-17"
TZ = "Europe/Amsterdam"

#: The order the day happens in. Sorting these handles reverses them.
LISTED = ["ZL1", "MG1", "BW1"]


def _ops() -> list[dict]:
    """Lunch, then Gym, then Walk — as raw dicts, the shape a tool call has.

    Only the first add names an anchor, which is the whole point: it says
    where the chain starts because the list cannot, and the other two say
    nothing because the list already did.
    """
    return [
        {
            "op": "add", "h": "ZL1", "n": "Lunch", "t": "H", "after": "END",
            "anchor_source": "user",
            "p": {"a": "fs", "st": "12:00:00", "dur": "PT1H"},
        },
        {"op": "add", "h": "MG1", "n": "Gym", "t": "H",
         "p": {"a": "ap", "dur": "PT1H"}},
        {"op": "add", "h": "BW1", "n": "Walk", "t": "H",
         "p": {"a": "ap", "dur": "PT20M"}},
    ]


def _ops_sharing_one_anchor() -> list[dict]:
    """The same three, every one saying `after: "END"`.

    A second shape, because the two mechanisms that make order matter are
    different code and a patch exercises one or the other. The chained
    shape above tests `_add_anchors` — each add takes the previous one's
    handle. This one tests `_insert_batch`'s tie-break: all three resolve
    to the same trailing position, and only their listed order separates
    them. This is the measured defect verbatim — it used to come out
    Walk, Gym, Lunch.
    """
    return [{**op, "after": "END"} for op in _ops()]


def _patch() -> dict:
    return {"ops": _ops()}


#: Both shapes, for every test that produces a day rather than a digest.
SHAPES = pytest.mark.parametrize(
    "ops", [_ops, _ops_sharing_one_anchor], ids=["chained", "sharing-one-anchor"]
)


def _reversed_patch() -> dict:
    """The same three ops, listed backwards. A different day, and every
    hop that carries a digest has to agree it is a different day."""
    return {"ops": list(reversed(_ops()))}


def _snapshot() -> dict:
    """Opaque to every hop under test — they carry it, none of them read it."""
    return {
        "calendar_id": "primary", "day": DAY, "tz": TZ,
        "etags": {}, "event_ids": [],
    }


def _handles(ops) -> list[str]:
    """The `h` sequence, from raw dicts or parsed op models alike."""
    return [op["h"] if isinstance(op, dict) else op.h for op in ops]


# --- the fixture itself, before anything is asserted with it -------------


def test_sorting_these_handles_reverses_them():
    """Every assertion below is `== LISTED`, and each is worth exactly as
    much as this. Handles whose listed order happened to be alphabetical
    would let a `sorted()` pass every test in this file.
    """
    assert sorted(LISTED) == list(reversed(LISTED))
    assert _handles(_ops()) == LISTED


# --- the two candidate digests -------------------------------------------
#
# A digest cannot report an order; it can only fail to distinguish one. So
# the property here is inequality: two orderings must not collapse to one
# candidate. `json.dumps(sort_keys=True)` sorts object keys and leaves
# arrays alone, which is what makes that true — and is the thing a
# well-meaning "canonicalise the patch" change would break.


def test_two_orderings_are_two_candidates_to_tmbx():
    listed = _tmbx_candidate_digest(_snapshot(), _patch())
    backwards = _tmbx_candidate_digest(_snapshot(), _reversed_patch())
    assert listed != backwards
    assert listed == _tmbx_candidate_digest(_snapshot(), _patch())


def test_two_orderings_are_two_candidates_to_the_dsh_commit_gate():
    listed = _dsh_candidate_digest({"snapshot": _snapshot(), "patch": _patch()})
    backwards = _dsh_candidate_digest(
        {"snapshot": _snapshot(), "patch": _reversed_patch()}
    )
    assert listed is not None
    assert listed != backwards


def test_the_gate_and_tmbx_agree_on_which_candidate_this_is():
    """The commit gate's digest is what the MCP `idempotency_key` is
    compared against, so the two implementations have to stay byte
    compatible. They are separate functions in separate packages; nothing
    but a test holds them together.
    """
    assert _dsh_candidate_digest(
        {"snapshot": _snapshot(), "patch": _patch()}
    ) == _tmbx_candidate_digest(_snapshot(), _patch())


# --- the DSH hook: write the candidate, read it back ----------------------


def test_the_hook_write_and_read_back_return_the_ops_as_listed(tmp_path):
    """`record_validation_result` runs in a short-lived hook process and
    hands the candidate to the harness through a file. Both ends are real
    here, including the digest check `read_validated_candidate` does — a
    hop that reordered the ops on the way out would fail that check rather
    than return a differently-ordered patch, which is the good failure.
    """
    state = tmp_path / "draft-state.json"
    candidate_file = tmp_path / "candidate.json"

    record_validation_result(
        {
            "tool_name": "mcp__tmbx__plan_apply",
            "hook_event_name": "PostToolUse",
            "tool_input": {"snapshot": _snapshot(), "patch": _patch()},
            "tool_response": json.dumps(
                {"ok": True, "committable": True, "rendered": "H,own,t\n"}
            ),
        },
        str(state),
        str(candidate_file),
    )

    candidate = read_validated_candidate(candidate_file)
    assert candidate is not None
    assert _handles(candidate.patch["ops"]) == LISTED


# --- PlanningArtifact, and the session envelope that stores it ------------


def _artifact() -> PlanningArtifact:
    return PlanningArtifact.create(
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload=_patch(),
        dependency_revisions={},
    )


def test_a_planning_artifact_keeps_the_ops_through_create_and_json():
    artifact = _artifact()
    assert _handles(artifact.payload["ops"]) == LISTED

    revalidated = PlanningArtifact.model_validate_json(artifact.model_dump_json())
    assert _handles(revalidated.payload["ops"]) == LISTED
    # The digest re-derives on validation, so a round trip that reordered
    # the ops would be refused rather than silently accepted.
    assert revalidated.digest == artifact.digest


def test_two_orderings_are_two_artifacts():
    backwards = PlanningArtifact.create(
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload=_reversed_patch(),
        dependency_revisions={},
    )
    assert backwards.digest != _artifact().digest


def test_the_session_envelope_round_trips_the_ops_as_listed():
    """What the session store writes to and reads from its one SQL column.
    Driven as the store's own two static methods do it, so a change to
    either serialisation side is caught here.
    """
    envelope = _StoredSessionEnvelope(
        snapshot=PlanningSessionSnapshot(
            session_key="C1:1.0",
            revision=1,
            owner_user_id="U1",
            artifacts=[_artifact()],
        ),
        outcomes={},
    )

    store = SqlAlchemyTimeboxingSessionRepository
    parsed = store._parse_envelope(store._serialize_envelope(envelope))
    assert _handles(parsed.snapshot.artifacts[0].payload["ops"]) == LISTED


# --- the brief, which is the prompt --------------------------------------


def test_the_canonical_brief_hands_the_planner_the_ops_as_listed():
    """`_canonical_brief` sorts `allowed_outputs` — a set, which has no
    order to lose — and passes `sort_keys=True`. A patch riding in
    `current_artifacts[].payload` is an array and must come out untouched.
    This is the one hop where a reorder would not corrupt a day directly:
    it would show the model a different plan than the one it wrote, which
    is worse, because nothing downstream would disagree.
    """
    artifact = _artifact()
    brief = PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 8, 29, 9, 0, tzinfo=UTC),
        locked_day=PlanningDay.lock_default(
            value=date(2026, 8, 29), timezone=TZ, lock_revision=1
        ),
        facts=[],
        assumptions=[],
        current_artifacts=[
            ArtifactSnapshot(
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                revision=artifact.revision,
                digest=artifact.digest,
                payload=artifact.payload,
            )
        ],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={"ok": True, "blocks": []},
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        readiness={"gaps": []},
        allowed_outputs={ArtifactKind.VALIDATED_CANDIDATE},
    )

    rendered = json.loads(_canonical_brief(brief))
    assert _handles(rendered["current_artifacts"][0]["payload"]["ops"]) == LISTED


# --- submit_planning_result, and the file the host reads back ------------


def test_submit_planning_result_records_the_ops_as_listed(tmp_path, monkeypatch):
    """The planner's own submission, through the real tool and the real
    file. `_validated` canonicalises with `sort_keys=True` before strict
    parsing, and its own comment says list order stays significant —
    this is what holds that comment to its word.
    """
    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))

    submit_planning_result(
        target_artifact="validated_candidate",
        artifact=_patch(),
        assumptions=[],
        blockers=[],
    )

    recorded = PlanningResult.model_validate_json(
        destination.read_text(encoding="utf-8")
    )
    assert _handles(recorded.artifact_updates[0].payload["ops"]) == LISTED


def test_a_resubmission_in_a_different_order_is_a_different_submission(
    tmp_path, monkeypatch
):
    """The idempotent retry path compares whole documents. Two orderings
    must not compare equal, or a genuinely different plan slips through as
    "the same submission, retried".
    """
    from fateforger.slack_bot.planning_result_mcp import PlanningResultRefused

    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))

    submit_planning_result(
        target_artifact="validated_candidate",
        artifact=_patch(),
        assumptions=[],
        blockers=[],
    )
    with pytest.raises(PlanningResultRefused):
        submit_planning_result(
            target_artifact="validated_candidate",
            artifact=_reversed_patch(),
            assumptions=[],
            blockers=[],
        )


# --- the journal, and the day it replays into ----------------------------


@SHAPES
def test_the_journalled_ops_replay_into_the_day_that_was_applied(ops):
    """`ops_json` is `patch.model_dump_json()`, and the journal is a
    training record — a row whose ops disagree with the day they produced
    teaches the wrong thing forever, and nothing would ever contradict it.
    Both serialisers on that path are exercised: `PlanService._journal`
    uses the model's own dump, `JournalingPatcher` goes through `_ops_json`.
    """
    patch = Patch.model_validate({"ops": ops()})
    for serialised in (patch.model_dump_json(), _ops_json(patch)):
        replayed = Patch.model_validate_json(serialised)
        assert _handles(replayed.ops) == LISTED

        day = apply_ops(
            Plan(date=date(2026, 8, 17), blocks=[]),
            replayed,
            mint_uid=lambda: "u",
        )
        assert [block.h for block in day.blocks] == LISTED


# --- end to end, through the real MCP server -----------------------------


def _event(event_id, handle, start_h, end_h):
    return CalendarEvent(
        event_id=event_id,
        summary=f"Block {handle}",
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=f"u-{event_id}",
        handle=handle,
    )


@pytest.fixture
async def built(tmp_path):
    calendar = FakeCalendar({"primary": [_event("e1", "PR1", 9, 10)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    service = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    return build_server(service), store


@SHAPES
async def test_the_ops_reach_the_day_in_the_order_the_tool_call_listed_them(built, ops):
    """The whole path in one call, from a tool call's raw JSON to a
    rendered day: FastMCP argument binding, `Patch.model_validate`,
    `PlanService.apply`, `apply_ops`, `render_plan`.

    The rendered plan's first column is the handle — an identifier tmbx
    minted, not anything a user wrote — so reading it back is addressing,
    not interpretation.
    """
    server, _store = built
    read = await server.call_tool(
        "plan_read", {"calendar_id": "primary", "day": DAY}
    )
    snapshot = json.loads(read[0].text)["snapshot"]

    applied = json.loads(
        (await server.call_tool(
            "plan_apply", {"snapshot": snapshot, "patch": {"ops": ops()}}
        ))[0].text
    )
    assert applied["ok"] is True

    rendered = [
        line.split(",", 1)[0] for line in applied["rendered"].splitlines()
    ]
    assert [h for h in rendered if h in set(LISTED)] == LISTED


@SHAPES
async def test_a_committed_patch_is_journalled_in_the_order_it_was_sent(built, ops):
    """One more hop than the test above, and the one that outlives the
    turn. `plan_commit` writes the calendar and appends the journal row
    that becomes a training label.
    """
    server, store = built
    read = await server.call_tool(
        "plan_read", {"calendar_id": "primary", "day": DAY}
    )
    snapshot = json.loads(read[0].text)["snapshot"]

    committed = json.loads(
        (await server.call_tool(
            "plan_commit", {"snapshot": snapshot, "patch": {"ops": ops()}}
        ))[0].text
    )
    assert committed["committed"] is True

    rows = await store.by_day("primary", date(2026, 8, 17))
    journalled = [
        Patch.model_validate_json(row.ops_json)
        for row in rows
        if row.ops_json != "{}"
    ]
    assert journalled
    for patch in journalled:
        assert _handles(patch.ops) == LISTED

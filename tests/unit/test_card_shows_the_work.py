# tests/unit/test_card_shows_the_work.py
"""The one line naming the work the day is planned around.

Structure and wording only, plus Block Kit validity through `blockkit`, the
way `test_render_context_surfaces.py` checks the same panel: a block Slack
would refuse fails here, not as a 400 in the thread.

Nothing here judges text. Every assertion is over what the host already
resolved -- a board number, a ticket name, one bool -- and the two sentences
this module owns.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import date

from blockkit import Button, Context, Message, Section, Text

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
    PlanningContext,
    TurnRequest,
)
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactDraft,
    ArtifactKind,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
)
from fateforger.agents.timeboxing.work_refs import work_refs_fact_id
from fateforger.slack_bot.stage_context import context_panel, shown_with_of
from fateforger.slack_bot.timeboxing_cards import render_context_panel

DAY = date(2026, 9, 8)
FINANCE = {"link": "m-page-427", "label": "Verify VPB 2024 aangifte", "task": 427}
DNS = {"link": "m-page-431", "label": "Move the DNS records", "task": 431}


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=DAY,
        timezone="Europe/Amsterdam",
        lock_revision=1,
        day_type=DayType.WORKING,
    )


def _rows() -> list[dict]:
    return [
        {
            "uid": "c-gym",
            "name": "Oats before gym",
            "necessity": "must",
            "anchors": [{"uid": "a1", "name": "gym"}],
        },
        {"uid": "c-plan", "name": "Plan at 17:00", "necessity": "should", "anchors": []},
    ]


def _work_fact(refs: list[dict]) -> PlanningFact:
    return PlanningFact(
        fact_id=work_refs_fact_id(DAY.isoformat()),
        kind=FactKind.WORK_REFS,
        value=refs,
        source="system",
    )


def _snapshot(
    *, refs: list[dict] | None = None, unresolved: bool = False
) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=_rows(),
        facts=[] if refs is None else [_work_fact(refs)],
        work_refs_unresolved=unresolved,
    )


def _panel_message(**kwargs):
    return render_context_panel(context_panel(_snapshot(**kwargs), first_shown_with=None))


def _head(message) -> str:
    return message.blocks[0]["text"]["text"]


def _text(node: dict) -> Text:
    return Text(type=node["type"], text=node["text"])


def _as_block(block: dict):
    """Rebuild one rendered block as blockkit objects so its validators run."""

    if block["type"] == "context":
        return Context(elements=[_text(e) for e in block["elements"]])
    accessory = block.get("accessory")
    return Section(
        text=_text(block["text"]),
        accessory=(
            Button(
                text=_text(accessory["text"]),
                action_id=accessory["action_id"],
                value=accessory.get("value"),
            )
            if accessory
            else None
        ),
    )


def _validated(blocks: list[dict]) -> None:
    # blockkit validates on .build(): a section over 3000 chars raises.
    Message(blocks=[_as_block(b) for b in blocks]).build()


# --- what the panel says ----------------------------------------------------


def test_the_panel_names_the_number_and_the_label_of_the_resolved_work() -> None:
    message = _panel_message(refs=[FINANCE])

    assert "#427 Verify VPB 2024 aangifte" in _head(message)
    assert len(message.blocks) == 2
    _validated(message.blocks)


def test_the_panel_never_shows_the_handle_a_ticket_is_attached_by() -> None:
    """A handle is a machine identifier: the planner writes it onto a block,
    and it says nothing to the person deciding whether the day is right."""

    message = _panel_message(refs=[FINANCE, DNS])

    assert "m-page-427" not in json.dumps(message.blocks)
    assert "m-page-431" not in json.dumps(message.blocks)


def test_several_tickets_are_joined_by_the_panels_own_separator() -> None:
    """` · `, not a comma: a ticket whose name holds a comma would otherwise
    make the boundaries between two names unreadable."""

    message = _panel_message(refs=[FINANCE, DNS])

    assert (
        "#427 Verify VPB 2024 aangifte · #431 Move the DNS records"
        in _head(message)
    )


def test_a_day_with_no_work_says_nothing_extra() -> None:
    """A session nobody named work for reads exactly as it did before: title,
    counts, anchor summary, and no fourth line."""

    without = _panel_message()
    named = _panel_message(refs=[FINANCE])

    assert len(_head(without).splitlines()) == 3
    assert len(_head(named).splitlines()) == 4
    assert _head(named).startswith(_head(without))
    assert len(without.blocks) == 2
    _validated(without.blocks)


def test_an_unresolved_turn_says_what_happened_in_the_persons_terms() -> None:
    """Not a stack trace and not "the board is down": three of the four causes
    are not the board, and none of them is the reader's problem to diagnose."""

    message = _panel_message(unresolved=True)

    assert (
        "I could not work out which ticket you meant — say which one and "
        "I'll attach it." in _head(message)
    )
    assert "board" not in _head(message)
    _validated(message.blocks)


def test_a_stale_ref_is_not_named_beside_the_unresolved_line() -> None:
    """The turn that resolved this ref is over, and facts merge by id rather
    than being deleted, so the ref on the snapshot may be the previous turn's.
    Naming it beside "I could not work out which ticket you meant" is the one
    combination that could get a wrong day approved."""

    panel = context_panel(_snapshot(refs=[FINANCE], unresolved=True), first_shown_with=None)
    message = render_context_panel(panel)

    # The typed value drops them too, so the panel never carries a name it
    # does not show and no later renderer can reintroduce one.
    assert panel.work == []
    assert "427" not in _head(message)
    assert "Verify VPB 2024 aangifte" not in _head(message)
    assert "could not work out" in _head(message)
    _validated(message.blocks)


def test_a_ticket_with_no_number_is_named_by_its_label_alone() -> None:
    """`TaskRow.number` is optional, and a ticket nobody numbered is still a
    ticket. The brief renders it the same way."""

    message = _panel_message(refs=[{"link": "m-x", "label": "Rename the repo", "task": None}])

    assert "Rename the repo" in _head(message)
    assert "None" not in _head(message)
    assert "#" not in _head(message).splitlines()[-1]
    _validated(message.blocks)


def test_a_long_list_is_cut_by_count_and_the_panel_stays_two_blocks() -> None:
    refs = [
        {"link": f"m-{i}", "label": f"Ticket number {i}", "task": 400 + i}
        for i in range(9)
    ]

    message = _panel_message(refs=refs)

    assert "#400 Ticket number 0" in _head(message)
    assert "+6 more" in _head(message)
    assert "#408" not in _head(message)
    assert len(message.blocks) == 2
    _validated(message.blocks)


# --- the line has to reach the user -----------------------------------------


def test_the_panel_is_redrawn_when_the_work_changes() -> None:
    """`sync_panel` edits the panel only when `shown_with_of` moves. The panel
    now draws the day's work, so work that changed with the rules unchanged
    has to move it -- otherwise the line is written and never seen."""

    nothing = shown_with_of(_snapshot())
    resolved = shown_with_of(_snapshot(refs=[FINANCE]))
    other = shown_with_of(_snapshot(refs=[DNS]))
    unresolved = shown_with_of(_snapshot(unresolved=True))

    assert nothing != resolved
    assert resolved != other
    assert nothing != unresolved


# --- the flag reaches the snapshot the card reads ---------------------------


class _Planner:
    def __init__(self) -> None:
        self.briefs: list[object] = []

    async def produce(self, brief, progress):
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.SKELETON,
                    payload={"markdown": "## Tuesday"},
                    dependency_revisions={"planning_day": 1},
                )
            ]
        )


class _Context:
    """A host resolve that answers about the work and nothing else.

    `facts` is what the host would splice in: `work_refs_for_turn` files the
    day's `WORK_REFS` fact with an empty value when it could not answer, and
    files nothing at all on a turn that never ran the lookup.
    """

    def __init__(self, unresolved: bool, facts: list[PlanningFact] | None = None) -> None:
        self.unresolved = unresolved
        self.facts = facts or []

    async def propose_planning_day(self, request):
        raise AssertionError("the day is locked in this test")

    async def resolve(self, snapshot, *, target, progress):
        return PlanningContext(
            facts=list(self.facts),
            applicable_constraints=_rows(),
            work_refs_unresolved=self.unresolved,
        )


class _Commit:
    async def commit(self, candidate, *, digest):
        raise AssertionError("this turn commits nothing")


class _Sink:
    async def emit(self, event):
        return None


_interaction_ids = itertools.count(1)


def _kernel_session() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=_day(),
        facts=[
            PlanningFact(
                fact_id="activity-1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="finish the next finance ticket",
                source="user",
            ),
            PlanningFact(
                fact_id="frame-1",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:00", "sleep": "23:30"},
                source="user",
            ),
        ],
    )


async def _advance_async(kernel, snapshot) -> None:
    await kernel.turn(
        TurnRequest(
            session_key=snapshot.session_key,
            interaction_id=f"1.{next(_interaction_ids)}",
            actor_user_id="U1",
            expected_revision=snapshot.revision,
            intent=Advance(),
        ),
        progress=_Sink(),
    )


def _advance(kernel, snapshot) -> None:
    asyncio.run(_advance_async(kernel, snapshot))


def test_the_kernel_mirrors_the_unresolved_flag_onto_the_snapshot() -> None:
    """The card reads the snapshot and the flag travels on `PlanningContext`,
    so without this the unresolved case is invisible to every card."""

    repository = InMemoryPlanningSessionRepository([_kernel_session()])
    context = _Context(unresolved=True)
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=_Planner(),
        context=context,
        commit=_Commit(),
    )

    _advance(kernel, _kernel_session())
    after = asyncio.run(repository.load_or_create("C1:1.0", owner_user_id="U1"))
    assert after.work_refs_unresolved is True

    context.unresolved = False
    _advance(kernel, after)
    cleared = asyncio.run(repository.load_or_create("C1:1.0", owner_user_id="U1"))
    assert cleared.work_refs_unresolved is False


async def test_a_failed_lookup_then_a_later_turn_names_no_ticket_at_all() -> None:
    """The sequence the review traced, end to end.

    An earlier turn resolved #427. The next candidate turn cannot read the
    board, so the host files the day's fact with an empty value and sets the
    flag: the panel says so and names nothing. A later turn -- a Back, a
    skeleton -- never runs the lookup, so it clears the flag. That is only
    safe because the ref is already gone: before the fix it would have brought
    #427 back onto the panel as this turn's answer, with nothing marking it.
    """

    from fateforger.slack_bot.timeboxing_host import work_refs_for_turn

    class _RefusingBoard:
        async def list_tasks(self, scope, *, limit=25, cursor=None):
            raise ConnectionError("connection refused")

    async def _unused_ask(prompt: str) -> str:
        raise AssertionError("no rows, so nothing to ask about")

    async def _unused_store(**_kwargs) -> str:
        raise AssertionError("no rows, so nothing to store")

    failed = await work_refs_for_turn(
        day=DAY.isoformat(),
        message="finish the next finance ticket",
        board=_RefusingBoard(),
        ask=_unused_ask,
        put_material=_unused_store,
    )
    assert failed.unresolved is True

    started = _kernel_session().model_copy(
        update={"facts": [*_kernel_session().facts, _work_fact([FINANCE])]}
    )
    repository = InMemoryPlanningSessionRepository([started])
    context = _Context(unresolved=True, facts=failed.facts)
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=_Planner(),
        context=context,
        commit=_Commit(),
    )

    await _advance_async(kernel, started)
    after_failure = await repository.load_or_create("C1:1.0", owner_user_id="U1")
    failed_head = _head(
        render_context_panel(context_panel(after_failure, first_shown_with=None))
    )
    assert "could not work out" in failed_head
    assert "427" not in failed_head

    # The later turn: no lookup, so no facts and no flag.
    context.unresolved = False
    context.facts = []
    await _advance_async(kernel, after_failure)
    later = await repository.load_or_create("C1:1.0", owner_user_id="U1")
    later_head = _head(
        render_context_panel(context_panel(later, first_shown_with=None))
    )

    assert later.work_refs_unresolved is False
    assert "427" not in later_head
    assert "Verify VPB 2024 aangifte" not in later_head
    assert "Planning around" not in later_head

# tests/unit/test_card_shows_the_board.py
"""The "from your board" section: the list the lookup could not choose from.

The section renders in exactly one state -- the work lookup could not work out
which ticket was meant, and the board it read offered rows -- and in every
other state it is absent. Hugo's ruling, 2026-09-11: the board is never shown
unprompted. Half of this file is the one state; the other half is the states
that draw nothing, one test each, because "absent" is the assertion that rots
silently if nobody pins it.

Structure and wording only, plus Block Kit validity through `blockkit`, the
way `test_card_shows_the_work.py` checks the line above it: a block Slack
would refuse fails here, not as a 400 in the thread.

Nothing here judges text. Every assertion is over what the host already
resolved -- a state enum, two dates, one bool -- or over the sentences this
module itself owns.
"""

from __future__ import annotations

import json
from datetime import date

from blockkit import Button, Context, Message, Section, Text

from fateforger.agents.tasks.task_source import (
    CandidateState,
    TaskCandidate,
    TaskCandidates,
)
from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.agents.timeboxing.work_refs import work_refs_fact_id
from fateforger.slack_bot.messages import SLACK_MAX_BLOCK_TEXT_CHARS
from fateforger.slack_bot.stage_context import context_panel, shown_with_of
from fateforger.slack_bot.timeboxing_cards import (
    BOARD_ROW_CAP,
    WORK_LINE_CAP,
    render_context_panel,
)

DAY = date(2026, 9, 8)
FINANCE_REF = {"link": "m-page-427", "label": "Verify VPB 2024 aangifte", "task": 427}


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=DAY,
        timezone="Europe/Amsterdam",
        lock_revision=1,
        day_type=DayType.WORKING,
    )


def _rules() -> list[dict]:
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


def _row(
    number: int | None,
    label: str,
    *,
    state: CandidateState = "next",
    due: date | None = None,
    external_id: str | None = None,
) -> TaskCandidate:
    return TaskCandidate(
        source="notion",
        external_id=external_id or f"page-{number}",
        number=number,
        label=label,
        summary="",
        state=state,
        due=due,
        overdue=due is not None and due < DAY,
        blocked_by=[],
        url=f"https://www.notion.so/page-{number}",
    )


def _listing(
    rows: list[TaskCandidate],
    *,
    sprint: str | None = "Sprint 8 - product",
    truncated: bool = False,
) -> TaskCandidates:
    return TaskCandidates(day=DAY, sprint=sprint, rows=rows, truncated=truncated)


def _snapshot(
    *,
    refs: list[dict] | None = None,
    unresolved: bool = False,
    candidates: TaskCandidates | None = None,
) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=_rules(),
        facts=[] if refs is None else [_work_fact(refs)],
        work_refs_unresolved=unresolved,
        candidates=candidates,
    )


def _panel(**kwargs):
    return context_panel(_snapshot(**kwargs), first_shown_with=None)


def _message(**kwargs):
    return render_context_panel(_panel(**kwargs))


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
    Message(blocks=[_as_block(b) for b in blocks]).build()


TWO_ROWS = [
    _row(427, "Verify VPB 2024 aangifte", due=date(2026, 9, 10)),
    _row(431, "Move the DNS records"),
]


# --- the one state that renders: the lookup could not decide ----------------


def test_the_section_names_the_sprint_and_every_row_it_shows() -> None:
    message = _message(unresolved=True, candidates=_listing(TWO_ROWS))
    head = _head(message)

    assert "From your board — Sprint 8 - product:" in head
    assert "#427 Verify VPB 2024 aangifte" in head
    assert "#431 Move the DNS records" in head
    assert len(message.blocks) == 2
    _validated(message.blocks)


def test_a_row_carries_its_due_date_and_says_when_it_is_late() -> None:
    message = _message(
        unresolved=True,
        candidates=_listing(
            [
                _row(427, "Verify VPB 2024 aangifte", due=date(2026, 9, 10)),
                _row(431, "Move the DNS records", due=date(2026, 9, 3)),
            ]
        ),
    )
    head = _head(message)

    assert "due 2026-09-10" in head
    assert "due 2026-09-03 · overdue" in head


def test_a_row_that_is_not_next_says_which_bucket_it_is_in() -> None:
    """`next` is the ordinary case and is left untagged; every other state is
    the exception a reader needs to see before planning around the row."""

    message = _message(
        unresolved=True,
        candidates=_listing(
            [
                _row(427, "Verify VPB 2024 aangifte"),
                _row(431, "Move the DNS records", state="waiting_for"),
            ]
        ),
    )
    head = _head(message)

    assert "#431 Move the DNS records · waiting for" in head
    assert "#427 Verify VPB 2024 aangifte\n" in head


def test_the_section_never_shows_the_ids_the_board_minted() -> None:
    """A page id and a url say nothing to the person approving a day, the same
    reason the work line never shows the material handle."""

    message = _message(unresolved=True, candidates=_listing(TWO_ROWS))
    rendered = json.dumps(message.blocks)

    assert "page-427" not in rendered
    assert "notion.so" not in rendered


def test_a_row_with_no_number_is_named_by_its_label_alone() -> None:
    message = _message(
        unresolved=True, candidates=_listing([_row(None, "Rename the repo")])
    )
    head = _head(message)

    assert "Rename the repo" in head
    assert "None" not in head
    assert "#" not in head.splitlines()[-1]


# --- nothing is ever marked as taken ----------------------------------------


def test_no_row_is_marked_as_the_one_the_day_took() -> None:
    """There is no tick, and there is no state left in which one could mean
    anything. The section renders only where the lookup could not decide, so
    no ref resolved this turn and no row was taken from this list -- and the
    ref standing on the snapshot may be an earlier turn's, which is exactly
    why `_work` names no ticket beside it either."""

    panel = _panel(refs=[FINANCE_REF], unresolved=True, candidates=_listing(TWO_ROWS))
    head = _head(render_context_panel(panel))

    assert not any(hasattr(item, "chosen") for item in panel.board)
    assert "✓" not in head
    assert "could not work out" in head
    assert "• #427 Verify VPB 2024 aangifte" in head
    assert "• #431 Move the DNS records" in head
    _validated(render_context_panel(panel).blocks)


# --- every other state draws nothing ----------------------------------------


def test_a_board_read_with_a_ticket_resolved_shows_no_section() -> None:
    """The ruling's core case, and the one the old code got wrong.

    The board was read and offered rows, and the lookup worked out which one
    was meant. The "Planning around" line above says which ticket the day is
    being planned around, and there is no question outstanding for a list of
    twelve sprint rows to answer -- so the rows are not a help, they are the
    sprint database posted under a heading that says "what I know about a
    working Friday". Hugo's ruling: the board is never shown unprompted.
    """

    panel = _panel(refs=[FINANCE_REF], candidates=_listing(TWO_ROWS))
    head = _head(render_context_panel(panel))

    assert panel.board != []  # the rows reached the panel; the renderer drops them
    assert "Planning around #427 Verify VPB 2024 aangifte" in head
    assert "From your board" not in head
    assert "#431 Move the DNS records" not in head


def test_a_board_read_with_no_work_named_shows_no_section() -> None:
    """Nobody asked the day to hold any work, and the board was read anyway.
    There is no work line and no question, so there is nothing the rows would
    be answering -- which is the unprompted case in its plainest form."""

    panel = _panel(candidates=_listing(TWO_ROWS))
    head = _head(render_context_panel(panel))

    assert "Planning around" not in head
    assert "could not work out" not in head
    assert "From your board" not in head


def test_a_board_that_could_not_be_read_says_so_once_and_shows_no_rows() -> None:
    """One vocabulary for one fact: the unresolved sentence already says the
    day has no ticket attached and names the way back. A second sentence about
    the board underneath it would say the same thing twice, in a register the
    reader cannot act on differently."""

    panel = _panel(unresolved=True)
    head = _head(render_context_panel(panel))

    assert panel.board == []
    assert "could not work out" in head
    assert "From your board" not in head


def test_a_turn_that_read_no_board_shows_no_section_beside_resolved_work() -> None:
    """The mirror is unconditional: any turn whose target is not a candidate
    clears `candidates` while the merged `WORK_REFS` fact survives. The work
    line still names the ticket -- it is a fact about the day -- and the
    section is simply absent."""

    panel = _panel(refs=[FINANCE_REF])
    head = _head(render_context_panel(panel))

    assert panel.board == []
    assert "Planning around #427 Verify VPB 2024 aangifte" in head
    assert "From your board" not in head


def test_a_board_that_offered_nothing_draws_no_section_at_all() -> None:
    """An empty sprint used to get a line of its own -- "nothing to plan
    around" -- on the reasoning that a read which found nothing is an answer
    worth printing. It is only an answer to a question nobody asked: the
    section is not a status display for the board, and with the rows gone from
    the unprompted states there is no reason for a sentence about their
    absence to stay behind. Both halves, resolved and unresolved."""

    for kwargs in ({}, {"refs": [FINANCE_REF]}, {"unresolved": True}):
        panel = _panel(candidates=_listing([]), **kwargs)
        head = _head(render_context_panel(panel))

        assert panel.board == []
        assert "From your board" not in head
        assert "nothing to plan around" not in head


def test_a_sprint_with_no_title_leaves_no_dangling_head() -> None:
    """`TaskCandidates.sprint` is the sprint page's title, and a page nobody
    titled gives back an empty string. The head has to read as a sentence
    without it, the same as when the scope resolved no sprint at all. Still
    reachable: the sprint's title has nothing to do with whether the lookup
    could decide."""

    for sprint in ("", " ", None):
        panel = _panel(unresolved=True, candidates=_listing(TWO_ROWS, sprint=sprint))
        head = _head(render_context_panel(panel))

        assert panel.board_sprint is None
        assert "From your board:" in head
        assert "From your board —" not in head
        _validated(render_context_panel(panel).blocks)


# --- the cap ----------------------------------------------------------------


def test_a_whole_sprint_fits_before_the_tail_becomes_a_count() -> None:
    """The cap is the section's own, not the work line's three: on the turn
    that asks "say which one", showing three of twelve defeats the question."""

    rows = [_row(400 + i, f"Ticket number {i}") for i in range(BOARD_ROW_CAP)]

    message = _message(unresolved=True, candidates=_listing(rows))
    head = _head(message)

    assert BOARD_ROW_CAP > WORK_LINE_CAP
    assert f"#{400 + BOARD_ROW_CAP - 1} Ticket number {BOARD_ROW_CAP - 1}" in head
    assert "more_" not in head
    assert len(message.blocks) == 2
    _validated(message.blocks)


def test_a_listing_past_the_cap_is_cut_by_count_and_the_panel_stays_two_blocks() -> None:
    rows = [_row(400 + i, f"Ticket number {i}") for i in range(BOARD_ROW_CAP + 2)]

    message = _message(unresolved=True, candidates=_listing(rows))
    head = _head(message)

    assert "#400 Ticket number 0" in head
    assert f"#{400 + BOARD_ROW_CAP}" not in head
    assert "_+2 more_" in head
    assert len(message.blocks) == 2
    _validated(message.blocks)


def test_a_full_section_stays_inside_the_block_slack_will_render() -> None:
    """The panel is one section block and Slack truncates it at 1600 chars.
    A capped section of realistic rows has to fit beside the panel's other
    lines, or the cap is silently doing the truncating in the wrong place."""

    rows = [
        _row(400 + i, f"Ticket number {i} with a name of a realistic length", due=date(2026, 9, 10))
        for i in range(BOARD_ROW_CAP + 5)
    ]

    message = _message(unresolved=True, candidates=_listing(rows))
    head = _head(message)

    assert len(head) < SLACK_MAX_BLOCK_TEXT_CHARS
    assert "_+5 more_" in head
    _validated(message.blocks)


# --- the section has to reach the user --------------------------------------


def test_the_panel_is_redrawn_when_the_candidates_change() -> None:
    """`sync_panel` edits the panel only when `shown_with_of` moves. A turn
    that changed the board and nothing else has to move it, or the section is
    written and never seen."""

    never_read = shown_with_of(_snapshot())
    read_empty = shown_with_of(_snapshot(candidates=_listing([])))
    read_rows = shown_with_of(_snapshot(candidates=_listing(TWO_ROWS)))
    other_rows = shown_with_of(
        _snapshot(candidates=_listing([_row(500, "Something else")]))
    )

    assert never_read != read_empty
    assert read_empty != read_rows
    assert read_rows != other_rows


def test_one_row_swapped_for_another_redraws_the_panel() -> None:
    """The property the three comparisons above do not pin.

    Each of those listings differs in length as well as in identity, so a term
    carrying only a count -- `board:read` plus `board:n=2` -- would satisfy all
    three and still leave the panel stale for the change that actually happens:
    a ticket closed on the board and another taking its place, which is the
    same number of rows and a different sprint. The set has to move on
    identity, so this listing is the same length as `TWO_ROWS` with one row
    replaced.
    """

    same_length = shown_with_of(
        _snapshot(candidates=_listing([TWO_ROWS[0], _row(500, "Something else")]))
    )

    assert len(TWO_ROWS) == 2
    assert shown_with_of(_snapshot(candidates=_listing(TWO_ROWS))) != same_length


def test_the_same_rows_in_a_new_order_redraw_the_panel() -> None:
    """The property identity alone does not pin, and the one production hits.

    `timeboxing_host.WORK_ROW_LIMIT` is a hundred, so the port hands back the
    whole sprint and this section shows `BOARD_ROW_CAP` of it. A Priority edit
    on Notion promotes a row from below the cap to above it: same rows, same
    count, new order. Under a term that is a bare set of ids nothing moves, the
    registry skips the edit, and the reader goes on seeing the old top-N in the
    old order while the judgement already judged over the new one -- which is
    exactly the invariant this section exists to hold, leaking on the ordering
    axis rather than the membership one.

    So the listing here is a twenty-row sprint with row thirteen promoted to
    the front, and the assertions are both halves: the panel content genuinely
    differs, and the set the registry compares by says so.
    """

    ordered = [_row(400 + i, f"Ticket number {i}") for i in range(20)]
    promoted = [ordered[BOARD_ROW_CAP], *ordered[:BOARD_ROW_CAP], *ordered[BOARD_ROW_CAP + 1 :]]

    # Same rows, same count: only the order moved.
    assert len(ordered) == len(promoted)
    assert sorted(row.external_id for row in ordered) == sorted(
        row.external_id for row in promoted
    )

    # The reader is shown a different list, so a skipped redraw is a stale panel.
    promoted_row = f"#{400 + BOARD_ROW_CAP} Ticket number {BOARD_ROW_CAP}"
    assert promoted_row not in _head(
        _message(unresolved=True, candidates=_listing(ordered))
    )
    assert promoted_row in _head(
        _message(unresolved=True, candidates=_listing(promoted))
    )

    assert shown_with_of(_snapshot(candidates=_listing(ordered))) != shown_with_of(
        _snapshot(candidates=_listing(promoted))
    )

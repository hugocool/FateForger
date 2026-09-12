# The TaskSource port and the "from your board" section (#401)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** The day's candidate tickets are read once, through one port, and both the judgement that decides which ones the user named and the surface that shows them to the user see that same list.

**Decision this implements:** #279, decided 2026-09-08. **Spec context:** `docs/superpowers/specs/2026-09-05-work-family-design.md` (the state mapping and the decided scope), `docs/architecture/materials-and-work-links.md` (what #398 shipped).

## Why a port at all, when `TaskBoard` already exists

`work_refs_for_turn` calls `board.list_tasks("current_sprint_ready", …)` directly (`timeboxing_host.py`). That read is what the judgement sees. If the card fetched its own list to show the user what is on the board, the two could differ — a row offered on the card that the judgement never saw, or the reverse — and nothing would detect it. One port, fetched once per turn, is what makes "the list you were shown is the list it judged over" true by construction rather than by coincidence.

The port also gives TickTick somewhere to land later without a second tool surface, which is #279's question 1.

## Global constraints

- **No `re`, no keyword lists, no substring or fuzzy matching over user content.** The candidate set is chosen by Notion's own structured filters and by comparisons over Notion's own enums, which are vocabularies that system minted. Nothing here decides what a person meant; the judgement that does already exists and is not touched by this plan.
- **No model call inside the port.** It is a read, called on the host's resolve path.
- **Failure is loud and named.** `TaskSourceUnavailable` carries the cause. Never an empty candidate list to stand for an error — that is the distinction the whole `work_refs_unresolved` flag exists to preserve.
- **Do not change what the judgement sees.** See the scope note below; this is the constraint most likely to be violated by accident.
- Tests: `cd <worktree> && PYTHONPATH=src ../../.venv/bin/python -m pytest <files> -q`. No `.env` and no venv in a worktree; unit tests must not need either.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. No pushing, no PR; the controller does both.

## The scope note, and a deliberate deviation from the #279 comment

The #279 decision names the candidate set as **current sprint, `Ready` or `Refined`**, following the work-family spec's scope decided with Hugo.

**This plan ships `Ready` only, and defers the widening.** The judgement that consumes this list was measured over exactly the 12 current-sprint `Ready` rows the board returned on 2026-09-08 (`tests/integration/test_eval_work_lookup.py`, 109/112 pooled on the finance case and 16/16 on four others). Adding `Refined` rows changes that list, and CLAUDE.md is explicit that a judgement whose input changed has not been validated by the run that measured the old input. Widening it here would silently move a measured judgement.

So: the port takes its scope as a parameter, ships with the measured one, and the widening becomes its own ticket that re-runs the eval at 8 draws over the widened rows and keeps whichever set holds. Recorded on #401 as a follow-up, not done here.

## Where the section renders, and why not where the old spec says

`docs/superpowers/specs/2026-09-03-stage-ux-port-design.md` puts a "from your board" section on the **stage-2 card**. Since it was written, #398 shipped the *resolved* work — the tickets the day is being planned around — on the **context panel** (`stage_context.py`, `ContextPanel.work`), on the deliberate grounds that the panel head is visible without unfolding and is posted once per stage.

The candidates and the resolved refs are two halves of one thing: what the board offered, and what was taken from it. Splitting them across a card and a panel would make the reader assemble them. **The section goes on the context panel, beside `work`.** The stage-UX spec's line is updated in Task 4 rather than left to contradict the code.

## Task 1 — the port

**Files:** `src/fateforger/agents/tasks/task_source.py` (new), `tests/unit/test_task_source.py` (new).

```python
Source = Literal["notion", "ticktick"]
CandidateState = Literal["next", "waiting_for", "someday", "done"]

class TaskCandidate(BaseModel):
    source: Source
    external_id: str          # the board's own id, never minted here
    number: int | None        # the integer the user says out loud
    label: str
    state: CandidateState
    due: date | None
    overdue: bool
    blocked_by: list[str]     # external ids

class TaskCandidates(BaseModel):
    day: date
    sprint: str | None        # the sprint's name, for the section's head
    rows: list[TaskCandidate]
    truncated: bool

class TaskSourceUnavailable(RuntimeError):
    """The board could not be read. Carries the cause."""

class TaskSource(Protocol):
    async def candidates(self, day: date, *, limit: int = 12) -> TaskCandidates: ...
```

`BoardTaskSource` implements it over `TaskBoard`:

- `__init__(self, board: TaskBoard, *, scope: Scope = "current_sprint_ready")`.
- `candidates` calls `board.list_tasks(scope, limit=limit)`, maps each `TaskRow`, and wraps **every** exception in `TaskSourceUnavailable` with the original as `__cause__` — including `TaskBoardError` and the transport errors that arrive from inside the MCP client, which is the likeliest real failure.
- `state` from the row, over Notion's own enums, per the work-family table: `Status` `Done` or `Archived` → `done`; else `Ticket Status` `Blocked` → `waiting_for`; else `Paused` or `Zombie` → `someday`; else `Ready` or `Refined` → `next`; else `someday` (an `Unrefined` row is not a candidate the planner should treat as ready, and the scope keeps it out anyway). Comparisons are equality against strings the board minted, which is explicitly outside the no-matching rule.
- `overdue = due is not None and due < day`. Arithmetic.
- `truncated` is true when the listing carried a `next_cursor`.
- `sprint` is the listing's sprint name when the scope resolved one, else `None`.
- `source` is `"notion"` for this adapter.

Tests, with a fake board (no network):

1. Each state-mapping row, including the precedence of `Done` over a `Blocked` ticket status.
2. `overdue` true, false, and false when `due` is None; the boundary (`due == day` is not overdue).
3. `truncated` reflects the cursor; `sprint` carries the name; `source` is `"notion"`.
4. `limit` and `scope` reach `list_tasks` unchanged.
5. A raising board becomes `TaskSourceUnavailable` with the cause attached, for a `TaskBoardError` and for a bare `RuntimeError` standing in for a transport failure.
6. An AST test: `task_source.py` imports neither `re` nor `difflib`.

Commit: `feat(tasks): a TaskSource port, and the board behind it (#401)`.

## Task 2 — one fetch per turn

**Files:** `src/fateforger/slack_bot/timeboxing_host.py`, `src/fateforger/agents/timeboxing/adaptive_timeboxing.py`, `src/fateforger/agents/timeboxing/session_contracts.py`, plus their tests.

- `work_refs_for_turn` takes `source: TaskSource` in place of `board`, and calls `source.candidates(day, limit=WORK_ROW_LIMIT)` inside the existing `asyncio.wait_for(..., BOARD_TIMEOUT_S)`. It passes `listing.rows` to `resolve_work` — **the same rows, in the same order**, since the prompt tells the model the order is the person's ranking.
- `resolve_work` today takes `list[TaskRow]`. Give it `list[TaskCandidate]` and adjust its prompt builder to read the candidate's fields. **The prompt text and the request shape do not change** beyond the field names it reads; the eval in `tests/integration/test_eval_work_lookup.py` must be updated to build `TaskCandidate` rows from the same live data it already carries, and must still assert the same rates. If a rate moves, stop and report — that is a finding, not a fixture to adjust.
- `WorkRefs` gains `candidates: TaskCandidates | None`. `None` means the board was never read (the empty-message path) or the read failed; the `unresolved` flag already distinguishes those two, and this field never stands in for either.
- `PlanningContext` gains `candidates: TaskCandidates | None`, set from the same `WorkRefs`.
- The kernel mirrors it onto the snapshot exactly as `work_refs_unresolved` is mirrored (`adaptive_timeboxing.py`, the `update={...}` at the resolve seam), and `PlanningSessionSnapshot` gains the field.
- **It does not reach the brief.** The planner is handed resolved refs and has no use for the candidate list; putting twelve rows on every brief would be a token cost for nothing. `PlanningBrief` is untouched. A test pins this: a brief built from a context carrying candidates contains none of their labels.

Tests: the host calls the source once per turn and hands the judgement the rows it got; a source that raises still produces the unresolved flag and no candidates; the empty-message path reads no board; the snapshot carries the candidates; the brief does not.

Commit: `feat(timeboxing): the board is read once, and the card and the judgement share the list (#401)`.

## Task 3 — the section

**Files:** `src/fateforger/slack_bot/stage_context.py`, `src/fateforger/slack_bot/timeboxing_cards.py`, `tests/unit/test_card_shows_the_work.py` (extend) or a new `tests/unit/test_card_shows_the_board.py`.

- `ContextPanel` gains `board: list[BoardCandidateItem]` and `board_sprint: str | None`, where the item is `number`, `label`, `state`, `due`, `overdue`, `chosen`.
- `chosen` is true when the candidate's `external_id` is one the day's `WORK_REFS` refs point at. The refs carry `link` (a material handle), `label` and `task` — **not** the external id. Join on `task` (the board number) when present, which is an integer the board minted; a ref with no number is not joined and simply marks nothing. Do not join on the label.
- The section renders under the existing work line: a head naming the sprint, then one line per candidate, capped by the panel's existing count cap, with the chosen ones marked. When `work_refs_unresolved` is set, the section still renders — the board was read or it was not, and the section says which — but no candidate is marked chosen, for the same reason `_work` returns nothing there.
- When the board could not be read, the panel says so in one line and shows no rows. Reuse the unresolved sentence rather than inventing a second vocabulary for the same fact.
- The panel's `first_shown_with` set must move when the candidates move, exactly as it moves for work; otherwise a turn that changes the board leaves a stale panel.

Tests: rows render with number, label and due; the chosen mark follows the refs; a ref with no number marks nothing; an unresolved turn shows rows but no marks; a board that could not be read shows the sentence and no rows; the panel is redrawn when the candidates change; the cap holds.

Commit: `feat(slack): the panel shows what is on your board, and what was taken from it (#401)`.

## Task 4 — docs

**Files:** `docs/architecture/materials-and-work-links.md`, `docs/superpowers/specs/2026-09-03-stage-ux-port-design.md`.

- Extend the architecture page with the port: what it is for, that it is read once per turn, that the card and the judgement share the list, the state mapping, and the deferred `Refined` widening with its reason.
- Correct the stage-UX spec's two lines that say the section stays empty until a task backend exists, and its line placing the section on the stage-2 card, pointing at the panel and saying why.
- No new page. This is an increment on an existing one.

Commit: `docs(timeboxing): the board section, and where it renders (#401)`.

## Out of scope, recorded on the ticket

- The `Refined` widening and its eval re-run.
- The memory-backed adapter (`memory_list_work`) that gives candidates a `next_action`: work-family increment 2.
- Any TickTick adapter; the `source` field admits one, nothing implements it.
- Any write to a board.

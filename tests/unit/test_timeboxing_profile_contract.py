"""What the DeepSeek profile prose is allowed to tell the planner.

The persona the harness assembles is three files concatenated: a generated
tmbx persona, then `memory-policy.md`, then `deployment.md` (see the
`system-prompt` stanza in `cordis.patch.yml`). Only the last two are versioned
in this repo as prose a human wrote, and they are the two that carry the
runtime contract — so they are what this module reads.

These assertions are about text this project authored, checked against
sentences this project also authored. Nothing here decides what a *user* meant,
so the ban in CLAUDE.md on pattern-matching user content does not reach it: a
prompt is a fixture, and a fixture is compared literally or not at all.

The absences matter more than the presences. Instruction prose is the one part
of this system that can go stale without anything failing — the model reads it,
believes it, and produces a confidently wrong plan. Two sentences went stale on
2026-08-29: one told the model the thread was the session state, which stopped
being true when the host began supplying a complete typed `PlanningBrief`, and
one recounted a 2026-08-24 gym-and-vacation incident, which a fresh process
read as this conversation's history. Issue #206.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "infra" / "dsh" / "profile"


def _profile_prose() -> str:
    """The two versioned persona halves, in the order the profile joins them."""

    return "".join(
        (PROFILE / name).read_text(encoding="utf-8")
        for name in ("memory-policy.md", "deployment.md")
    )


def _absent(needle: str) -> bool:
    """Whether a retired sentence is gone, ignoring the case it was written in.

    Case-folding our own prose to check our own absence is fixture handling, not
    a judgement about anything a user said. It earns its place: the first
    version of this module compared case-sensitively, and re-introducing "The
    thread is the state" at the start of a sentence walked straight past the
    assertion written to forbid it.
    """

    return needle.casefold() not in _flowed().casefold()


def _flowed() -> str:
    """The same prose with its hard wrapping collapsed.

    The files are wrapped to 78 columns, so a sentence worth asserting on is
    almost always split across lines. Collapsing runs of whitespace lets an
    assertion name the sentence rather than the column it happened to break at.
    """

    return " ".join(_profile_prose().split())


# -- the brief is the state ------------------------------------------------


def test_the_brief_is_authoritative_for_the_day_and_the_stage() -> None:
    """The host locked the day; the model re-deriving it is the #206 incident.

    On 2026-08-29 the planner turned Saturday into Friday and inferred a
    working day from the work it found on the calendar. Both were downstream of
    prose that handed it the derivation.
    """

    prose = _flowed()

    assert "is authoritative for the date, the timezone, the day type" in prose
    assert (
        "Do not infer a different day or stage from calendar content or prose" in prose
    )
    # Scoped, not unconditional: today every live turn is a no-brief turn, and
    # the half of the persona read first must still say what governs one.
    assert "When the host hands you a PlanningBrief" in prose
    assert "Without a brief nothing above you is sequencing the session" in prose


def test_the_persona_does_not_narrate_its_own_revision_history() -> None:
    """A dated incident in the prompt reads as this conversation's history.

    The first pass at this section deleted one anecdote and explained the
    deletion to the model in another, restating the retired instruction and
    dating a fresh Saturday/Friday failure. Both doors lead to the same place:
    a fresh process cannot tell a recounted day from the day it is planning.
    """

    prose = _flowed()

    assert _absent("turned a Saturday into a Friday")
    assert _absent("That paragraph replaced one saying")


def test_the_stage_section_no_longer_claims_the_thread_is_the_state() -> None:
    """Two sentences that were true before the kernel existed and are not now.

    A model told that nothing enforces the sequence, and that the thread is
    where the session lives, will reconstruct a stage from conversational
    shape. The host now computes it from artifacts and approvals and says so in
    the brief; the prompt must not offer a second, weaker answer.
    """

    assert _absent("the thread is the state")
    assert _absent("There is no machinery enforcing this")
    assert _absent("nothing enforces the sequence")


def test_only_the_requested_artifact_may_come_back() -> None:
    prose = _flowed()

    assert "Produce only the artifact the brief asks for" in prose


# -- the stage boundary ----------------------------------------------------


def test_stage_three_presents_and_stage_four_is_the_first_patch_stage() -> None:
    """The calendar-mutation boundary, stated where the model reads it.

    Measured at 6 draws on a planted case, one draw reached for `plan_apply`
    while presenting a skeleton (see `infra/dsh/README.md`). The `PreToolUse`
    deny is what actually holds the boundary, but a prompt that contradicts it
    turns a held boundary into a refused tool call the model then argues with.
    """

    prose = _flowed()

    assert "Stage 3 presents a skeleton; do not call plan_apply" in prose
    assert "Stage 4 is the first patch/validation stage" in prose


# -- the result obligation -------------------------------------------------


def test_every_planning_turn_ends_with_exactly_one_result_call() -> None:
    """Prose is presentation; the tool call is the only thing the host reads.

    An advance that ends in another recap is an advance that produced nothing,
    which is what the incident turn did.
    """

    prose = _flowed()

    # The name the model is offered, not the name the tool has in Python. The
    # mount is `serverName: planning_result`, so the bridge presents it as
    # mcp__planning_result__submit_planning_result -- and unlike a missed
    # progress call, naming this one wrongly costs the whole turn.
    assert (
        "A briefed turn ends by calling "
        "mcp__planning_result__submit_planning_result exactly once" in prose
    )
    # The tool is mounted only for a briefed turn. An unconditional obligation
    # would order every ordinary /dsh turn to call something that is not there.
    assert "Without a brief the tool is not mounted" in prose


# -- who owns a gap --------------------------------------------------------


def test_planner_owned_placements_are_decided_and_labelled_not_asked() -> None:
    """Ordinary timing is the planner's to choose, and its choice is visible.

    The user delegated gym and morning times and was asked for both anyway.
    Deciding wrongly costs one revision; asking costs a whole turn.
    """

    prose = _flowed()

    assert "Ordinary placement is yours to decide" in prose
    assert "label it as an assumption" in prose
    assert "may not become a user question" in prose
    assert "typed infeasibility" in prose


# -- the anecdote, and what survives it ------------------------------------


def test_the_2026_08_24_incident_prose_is_gone() -> None:
    """A recounted conversation reads as *this* conversation to a fresh process.

    Every harness turn starts empty, so a dated anecdote in the persona is
    indistinguishable from context about the day being planned. Task 7 proved
    the same text cannot reach the model through the brief unless a typed
    current-session fact carries it; this is the other door.
    """

    prose = _flowed()

    assert _absent("no gym today, it's vacation")
    assert _absent("Observed 2026-08-24")


def test_the_general_rules_the_anecdote_carried_survive_it() -> None:
    """The incident was the evidence, not the rule. Only the evidence goes.

    Each of these was learned from a real failure and is still load-bearing:
    a negation nothing on the calendar can show, a question asked a third time,
    and a boundary encoded as an occupying block.
    """

    prose = _flowed()

    assert "An absence is an answer." in prose
    assert "Ask at most once per thing, and never twice." in prose
    assert "work window is a boundary constraint" in prose


# -- the two halves load from one root -------------------------------------


def test_both_persona_halves_load_from_the_repository() -> None:
    """The assertions above are worthless if the model reads a different file.

    `memory-policy.md` was once read from `~/.dsh/profiles/tmbx/` while
    `deployment.md` was read from the repo. Everything in this module reads the
    repo copies, so the suite stayed green while the deployed half still carried
    the retired stage section — green in exactly the state where the live
    persona violated the contract. A prompt test that cannot see the prompt the
    model is given tests nothing, so this one checks the wiring instead of the
    words.
    """

    profile = (PROFILE / "cordis.patch.yml").read_text(encoding="utf-8")
    reads = [
        line
        for line in profile.splitlines()
        if "memory-policy.md" in line or "deployment.md" in line
    ]
    persona_reads = [line for line in reads if "'utf8')" in line]

    assert len(persona_reads) == 2, (
        "expected the persona stanza to read exactly two markdown halves; "
        f"found {persona_reads}"
    )
    for line in persona_reads:
        assert "/infra/dsh/profile/" in line
        assert ".dsh/profiles" not in line


def test_the_deployed_profile_matches_the_repository() -> None:
    """The tests above read the repo; the harness reads ~/.dsh. Catch the gap.

    `ask` runs `--profile tmbx` against `DSH_HOME`, so the file that actually
    governs a turn is `~/.dsh/profiles/tmbx/cordis.patch.yml` — not the one in
    this repository. Every other assertion in this module reads the repo copy,
    which is how a persona fix can be green here and absent from the running
    system. That already happened once with `memory-policy.md`; pointing both
    halves at `FF_FATEFORGER_ROOT` fixed the halves and left the file doing the
    pointing still deployed by hand.

    Skipped where no profile is installed, because CI has no `~/.dsh` and a
    failure there would say nothing about the deployment this guards.
    """

    deployed = Path.home() / ".dsh" / "profiles" / "tmbx" / "cordis.patch.yml"
    if not deployed.is_file():
        pytest.skip("no tmbx profile installed on this machine")

    repo = (PROFILE / "cordis.patch.yml").read_text(encoding="utf-8")
    assert deployed.read_text(encoding="utf-8") == repo, (
        "the installed tmbx profile differs from this repository's. The model "
        "is reading the installed one, so any mount or persona change here is "
        "inert until you run:\n"
        f"  cp {PROFILE / 'cordis.patch.yml'} {deployed}"
    )


def test_a_fresh_day_is_chained_relationally_not_pinned() -> None:
    """Least commitment is tmbx policy, and the planner was working against it.

    Measured on 2026-08-29 in the tmbx journal (entry 133): the planner tried to
    chain a day with `after: <handle>`, was refused because an anchor could only
    name a block already on the plan, and retried four seconds later with every
    block pinned to a wall-clock `fs` — a day that cannot shift, so buffers and
    constraint rules stop applying downstream.

    The refusal is gone: an add's `after` may now name a handle the same patch
    adds, and tmbx applies adds in dependency order (`tmbx.core.ops`). So the
    prose no longer has a restriction to work around, and the absences below
    are the load-bearing half — the retired sentences justified the pinned day,
    and a prompt that still carries them will keep producing it.
    """

    prose = _flowed()

    assert "may name another handle created in the same patch" in prose
    assert "applies adds in dependency order" in prose
    assert '{"a":"ap","dur":...}' in prose
    assert '`after: "END"`' in prose
    # Measured live 2026-08-30: the planner typed "Morning ritual" as BG and
    # anchored the whole day on it. BG sits outside the chain, so the plan had
    # no fs/fw anchor and tmbx refused the entire patch -- not the one block
    # that was mistyped. The prose said a boundary is not an occupying block;
    # it did not say what BG is *for* where the type is chosen.
    assert "never a thing he does" in prose
    assert "overspecified" in prose
    # Measured live 2026-08-30: the plan came back `overspecified: ['GY1']` and
    # the planner presented it unchanged. The prose forbade a gratuitous pin and
    # never said what to do when the tool reports one, so the measurement had no
    # consequence.
    assert "A non-empty `overspecified` is a correction, not a remark" in prose

    assert _absent("every anchor resolves only against the pre-patch snapshot")
    assert _absent("Never set `after` to another handle created in the same patch")
    assert _absent("an anchor cannot name a handle from the same patch")

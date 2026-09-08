"""One judgement: which board rows does this message name?

It runs **host-side**, once, before the planning turn — not as a tool the
planner holds and not as a subagent inside the turn. Two measurements on
2026-09-08 put it here (`docs/superpowers/plans/2026-09-08-work-links-on-blocks.md`):
a planner with the board mounted searched the board itself, which is the
distraction failure `timebox_patch` exists to avoid; and the child that was
meant to return an id answered with a task title and no id anywhere, because a
persona asked for an id is a request, not a contract. This is the shape
`resolve_anchor_names` already uses in the memory server: one sampling call
turns names into ids, and everything downstream is set membership over
identifiers the system minted.

Three things this module holds to.

**The scope is the caller's, never the model's.** `resolve_work` is handed the
rows to consider and never chooses them. The same spike watched a subagent
pick its own reading of "next" and pass over an overdue in-sprint tax filing
for something outside the sprint. Here, work outside the rows shown resolves
to nothing, and the block is planned unlinked.

**The rows are a list to point at, not a vocabulary to classify into.** The
options are ids in the prompt text, so "none of these" is the empty list — a
structural answer rather than an option a model must be persuaded to choose. A
peer measured that carrying the options in the prompt text rather than only in
a response schema moved 6/10 misroutes to 0/10.

**Every id that comes back is checked against the rows shown**, and an id that
is not among them raises with the id named, exactly as `ingest`'s
`duplicate_of` and `projection`'s `constraint_uid` checks do. Comparing page
ids is set membership over identifiers Notion minted, which is the documented
exception to CLAUDE.md's matching ban; deciding that a message *names* a piece
of work is the model's judgement and nothing else's.

The transport stays out: `ask` is injected, so the caller decides which model,
which effort, and which host. The prompt and the parsing live here, the way
`PromptJudge` keeps them off its transports.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from fateforger.agents.tasks.board import TaskRow

# How much of a row's summary reaches the prompt. Cutting by length is
# arithmetic over a stored field — it decides nothing about what the text
# means, it only keeps a listing of a whole sprint small.
SUMMARY_LIMIT = 200

Ask = Callable[[str], Awaitable[str]]

WORK_LOOKUP_PROMPT = """\
You decide which pieces of work, if any, a message names.

You are given a message and a list of tickets. Each ticket is one line: its
id, its task number, its name, and a short summary. Answer with the ids of the
tickets the message names.

The list is the whole world for this question. Other work exists, but it is
not on offer here: if the message names something the list does not contain,
the answer is an empty list. That is a normal answer and not a failure — the
block is then planned with no ticket attached and someone attaches one later.
Do not stretch a ticket to fit, and do not answer with the nearest thing.

"the next X" needs no interpretation beyond the list: the list is already the
person's current ready work, in the order their board ranks it, so "the next
X" is the first ticket in the list that is an X. A ticket's name may not use
the words the message uses — a tax filing is finance work whether or not it
says so — so read what each ticket is about, not what it is called.

A message can name several tickets, one, or none of them. Answer with ids
copied exactly from the list: never a name, never a task number, and never an
id that is not in the list.

Respond with JSON only: {"page_ids": ["...", "..."]}\
"""


class UnknownWorkId(ValueError):
    """The model answered with an id that was not among the rows it was shown."""


class WorkJudgement(BaseModel):
    """Which of the rows shown the message names. Empty is a real answer."""

    page_ids: list[str] = Field(default_factory=list)


def _render_row(row: TaskRow) -> str:
    """One ticket as one line: the id first, so the id is what gets copied."""
    number = f"#{row.number}" if row.number is not None else "#--"
    summary = row.summary[:SUMMARY_LIMIT].strip()
    line = f"{row.page_id}  {number}  {row.name}"
    return f"{line} — {summary}" if summary else line


def build_prompt(message: str, rows: list[TaskRow]) -> str:
    """The whole question, options included, as one string for the transport."""
    listing = "\n".join(_render_row(row) for row in rows)
    return (
        f"{WORK_LOOKUP_PROMPT}\n\n"
        f"Message:\n{json.dumps(message, ensure_ascii=False)}\n\n"
        f"Tickets:\n{listing}"
    )


def _first_json_object(content: str) -> dict[str, Any] | None:
    """The first complete JSON object in a reply, whatever surrounds it.

    The same discipline as `memory.prompts._first_json_object`, reimplemented
    rather than imported: `src/memory` is a standalone MCP server this package
    must not depend on. A model asked for JSON still fences it or writes a
    sentence around it, and a strict parse would turn that into a dead call.

    Scanning for `{` is not the banned kind of matching: it looks for where a
    JSON value begins in an envelope this system asked for — wire syntax, not
    a judgement about anything the user said.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        return payload
    return None


async def resolve_work(
    message: str, rows: list[TaskRow], *, ask: Ask
) -> list[TaskRow]:
    """The rows the message names, in the order the model pointed at them.

    `rows` is the caller's scope decision and is never widened here. An empty
    `rows` short-circuits: there is nothing to point at, so there is no
    question to ask. An empty result means the day is planned unlinked.

    Raises `UnknownWorkId` if an answer names a row that was not shown, and
    `ValueError` if the reply carries no readable judgement — a lookup that
    quietly returned nothing would be indistinguishable from a message that
    named no work.
    """
    if not rows:
        return []

    content = await ask(build_prompt(message, rows))
    payload = _first_json_object(content)
    if payload is None:
        raise ValueError(f"could not parse work lookup response: {content!r}")
    if "page_ids" not in payload:
        raise ValueError(f"could not parse work lookup response: {payload!r}")
    try:
        judgement = WorkJudgement.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"could not parse work lookup response into WorkJudgement: {payload!r}"
        ) from exc

    shown = {row.page_id: row for row in rows}
    resolved: list[TaskRow] = []
    taken: set[str] = set()
    for page_id in judgement.page_ids:
        row = shown.get(page_id)
        if row is None:
            raise UnknownWorkId(
                f"work lookup returned unknown page id {page_id!r}; "
                f"not among the {len(shown)} rows shown"
            )
        # One row per ticket even if the model names it twice: the caller
        # mints a material handle per row, and a repeat is noise, not a
        # second piece of work.
        if page_id not in taken:
            taken.add(page_id)
            resolved.append(row)
    return resolved

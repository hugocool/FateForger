"""The host judgement that decides which board rows a message names.

Every test drives a stubbed ``ask``: no ``.env``, no network, no model. What
they assert is the *plumbing* — that the rows reached the prompt as ids to
point at, and that what came back was verified against those rows before it
was acted on. Quality of the judgement is an eval's job (CLAUDE.md); nothing
here asserts a model's words.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fateforger.agents.tasks.board import TaskRow
from fateforger.agents.timeboxing.work_lookup import (
    SUMMARY_LIMIT,
    UnknownWorkId,
    build_prompt,
    resolve_work,
)

TAX_PAGE_ID = "30828174-6a47-8011-b3d0-000000000001"
DEBT_PAGE_ID = "30828174-6a47-8011-b3d0-000000000002"


class FakeAsk:
    """Records each prompt and answers the next canned reply."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._replies:
            raise AssertionError("unexpected extra call to ask")
        return self._replies.pop(0)


def row(
    *,
    page_id: str = TAX_PAGE_ID,
    number: int | None = 427,
    name: str = "Verify VPB 2024 aangifte",
    summary: str = "Check the corporate tax return before the accountant files it.",
) -> TaskRow:
    return TaskRow(
        page_id=page_id,
        number=number,
        name=name,
        status="Not started",
        ticket_status="Ready",
        summary=summary,
        dod="Filed and confirmed.",
        url=f"https://www.notion.so/{page_id}",
        last_edited="2026-09-05T09:14:00.000Z",
    )


def debt_row() -> TaskRow:
    return row(
        page_id=DEBT_PAGE_ID,
        number=413,
        name="Chase the outstanding invoices",
        summary="Send the second reminder to the two late clients.",
    )


async def test_the_prompt_carries_every_row_id_name_and_number() -> None:
    rows = [row(), debt_row()]
    ask = FakeAsk('{"page_ids": []}')

    await resolve_work("the next finance ticket", rows, ask=ask)

    assert len(ask.prompts) == 1
    prompt = ask.prompts[0]
    for candidate in rows:
        assert candidate.page_id in prompt
        assert candidate.name in prompt
        assert f"#{candidate.number}" in prompt
    assert "the next finance ticket" in prompt


async def test_a_returned_id_maps_to_its_row() -> None:
    rows = [row(), debt_row()]
    ask = FakeAsk(f'{{"page_ids": ["{TAX_PAGE_ID}"]}}')

    resolved = await resolve_work("the next finance ticket", rows, ask=ask)

    assert [r.page_id for r in resolved] == [TAX_PAGE_ID]
    assert resolved[0] is rows[0]


async def test_several_returned_ids_map_in_the_order_given() -> None:
    rows = [row(), debt_row()]
    ask = FakeAsk(f'{{"page_ids": ["{DEBT_PAGE_ID}", "{TAX_PAGE_ID}"]}}')

    resolved = await resolve_work("both finance tickets", rows, ask=ask)

    assert [r.page_id for r in resolved] == [DEBT_PAGE_ID, TAX_PAGE_ID]


async def test_an_id_that_is_not_among_the_rows_raises_naming_it() -> None:
    ask = FakeAsk('{"page_ids": ["30828174-6a47-8011-b3d0-00000000dead"]}')

    with pytest.raises(UnknownWorkId) as excinfo:
        await resolve_work("the next finance ticket", [row()], ask=ask)

    assert "30828174-6a47-8011-b3d0-00000000dead" in str(excinfo.value)


async def test_a_name_instead_of_an_id_is_refused() -> None:
    """The answer currency is ids, so a title is an unknown id, not a match."""
    ask = FakeAsk('{"page_ids": ["Verify VPB 2024 aangifte"]}')

    with pytest.raises(UnknownWorkId) as excinfo:
        await resolve_work("the tax one", [row()], ask=ask)

    assert "Verify VPB 2024 aangifte" in str(excinfo.value)


async def test_an_empty_answer_is_a_normal_outcome() -> None:
    ask = FakeAsk('{"page_ids": []}')

    assert await resolve_work("book a haircut", [row()], ask=ask) == []


async def test_no_rows_short_circuits_without_asking() -> None:
    ask = FakeAsk()

    assert await resolve_work("the next finance ticket", [], ask=ask) == []
    assert ask.prompts == []


async def test_the_same_id_twice_resolves_to_one_row() -> None:
    ask = FakeAsk(f'{{"page_ids": ["{TAX_PAGE_ID}", "{TAX_PAGE_ID}"]}}')

    resolved = await resolve_work("the tax one", [row()], ask=ask)

    assert [r.page_id for r in resolved] == [TAX_PAGE_ID]


async def test_json_wrapped_in_prose_is_still_read() -> None:
    ask = FakeAsk(
        "Sure — here is the answer:\n"
        f'```json\n{{"page_ids": ["{TAX_PAGE_ID}"]}}\n```\nHope that helps.'
    )

    resolved = await resolve_work("the tax one", [row()], ask=ask)

    assert [r.page_id for r in resolved] == [TAX_PAGE_ID]


async def test_an_answer_with_no_json_raises_saying_so() -> None:
    ask = FakeAsk("I could not find anything relevant.")

    with pytest.raises(ValueError) as excinfo:
        await resolve_work("the tax one", [row()], ask=ask)

    assert "no JSON object" in str(excinfo.value)


async def test_an_answer_without_the_field_raises_naming_the_field() -> None:
    """A different failure from unparseable text, and it says which fired."""
    ask = FakeAsk('{"tasks": ["Verify VPB 2024 aangifte"]}')

    with pytest.raises(ValueError) as excinfo:
        await resolve_work("the tax one", [row()], ask=ask)

    assert "page_ids" in str(excinfo.value)
    assert "no JSON object" not in str(excinfo.value)


async def test_a_missing_field_is_not_read_as_naming_nothing() -> None:
    """The empty answer is `{"page_ids": []}`; a missing key is a non-answer."""
    ask = FakeAsk("{}")

    with pytest.raises(ValueError):
        await resolve_work("the tax one", [row()], ask=ask)


def test_the_rows_are_a_list_to_point_at_not_a_vocabulary() -> None:
    """Ids are the options in the prompt text, and "none" is the empty list.

    The measured finding this encodes: pointing at ids gives "none of these" a
    structural answer, and carrying the options in the prompt text rather than
    only in a schema moved 6/10 misroutes to 0/10.
    """
    rows = [row(), debt_row()]

    prompt = build_prompt("the next finance ticket", rows)

    for candidate in rows:
        assert candidate.page_id in prompt
    assert '"page_ids"' in prompt


def test_the_prompt_shows_the_none_answer_shape() -> None:
    """The declared normal outcome needs a shape the model can copy.

    With only the populated form demonstrated, a model rendering "none" as
    `{}` or a bare `[]` hits the parse guard and turns a normal answer into an
    exception the caller would have to catch.
    """
    prompt = build_prompt("book a haircut", [row()])

    assert '{"page_ids": []}' in prompt


def test_the_prompt_separates_naming_an_item_from_naming_a_topic() -> None:
    """The discriminator an eval showed the prompt could not do without.

    At n=8 on the flash pin, "serious c2f work in the morning, gym in the
    evening" attached ticket #500 five times in eight and gave two further
    multi-ticket sets — the coin flip CLAUDE.md describes, from a prompt that
    named a category without giving the model anything to key off. Both sides
    are shown in the prompt now; the rate is asserted in
    tests/integration/test_eval_work_lookup.py.
    """
    prompt = build_prompt("serious c2f work in the morning", [row()])

    assert "SINGLES OUT" in prompt
    assert "NAMES A TOPIC" in prompt


def test_a_long_summary_is_truncated_in_the_prompt() -> None:
    """Cutting by length is arithmetic; it keeps the prompt small."""
    long_summary = "x" * (SUMMARY_LIMIT + 200)

    prompt = build_prompt("anything", [row(summary=long_summary)])

    assert "x" * SUMMARY_LIMIT in prompt
    assert "x" * (SUMMARY_LIMIT + 1) not in prompt


def test_a_row_without_a_task_number_still_renders() -> None:
    prompt = build_prompt("anything", [row(number=None)])

    assert TAX_PAGE_ID in prompt
    assert "Verify VPB 2024 aangifte" in prompt


def test_work_lookup_uses_no_pattern_matching() -> None:
    """No `re`, no `difflib`: which work a message names is the model's call."""
    from fateforger.agents.timeboxing import work_lookup as work_lookup_module

    source = Path(work_lookup_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    assert "re" not in imported
    assert "difflib" not in imported

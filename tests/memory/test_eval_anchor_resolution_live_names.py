# tests/memory/test_eval_anchor_resolution_live_names.py
"""Every anchor name in the real store resolves to its own anchor (#330).

The existing anchor-resolution eval offers three candidates and passed. The
defect only showed on the live list of thirty: asked to echo a 32-hex uid
chosen from thirty, the model returned one a character off for `lunch` in
three draws of four. So this eval runs against the live names, on a copy.

Set MEMORY_EVAL_DB to a store to copy (never the live path opened in place).
Names are read from the copy and never printed; a failure names the count.
"""
from __future__ import annotations

import asyncio
import os
import shutil

import pytest

from memory.anchor_store import AnchorStore
from memory.openrouter_judge import openrouter_judge_from_env

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
    pytest.mark.skipif(
        not os.environ.get("MEMORY_EVAL_DB", "").strip(),
        reason="MEMORY_EVAL_DB not set; this eval needs a store to copy",
    ),
]

SAMPLES = 8
THRESHOLD = 7


@pytest.fixture(scope="module")
def anchors(tmp_path_factory):
    target = tmp_path_factory.mktemp("store") / "memory.db"
    shutil.copy(os.environ["MEMORY_EVAL_DB"].strip(), target)
    return AnchorStore(str(target)).all()


async def _hits(judge, name: str, candidates, want: str | None) -> int:
    results = await asyncio.gather(
        *(judge.resolve_anchors([name], candidates) for _ in range(SAMPLES))
    )
    return sum(
        1 for r in results
        if r.resolutions and r.resolutions[0].anchor_uid == want
    )


async def test_every_live_name_resolves_to_its_own_anchor(anchors) -> None:
    assert len(anchors) >= 20, "the live store has ~30 anchors; a small copy proves nothing"
    async with openrouter_judge_from_env() as judge:
        rates = await asyncio.gather(
            *(_hits(judge, a.name, anchors, a.uid) for a in anchors)
        )
    misses = [(a.name, r) for a, r in zip(anchors, rates) if r < THRESHOLD]
    assert not misses, (
        f"{len(misses)} of {len(anchors)} live anchor names resolved to their own anchor "
        f"in fewer than {THRESHOLD}/{SAMPLES} draws"
    )


async def test_a_name_that_matches_nothing_is_new(anchors) -> None:
    async with openrouter_judge_from_env() as judge:
        new = await _hits(judge, "hang gliding", anchors, None)
    assert new >= THRESHOLD, f"'hang gliding' was read as new in only {new}/{SAMPLES} draws"

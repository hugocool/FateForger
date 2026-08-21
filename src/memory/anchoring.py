# src/memory/anchoring.py
from __future__ import annotations

import asyncio
import weakref

from memory.anchor import Anchor
from memory.anchor_store import AnchorStore
from memory.judge import Judge

# One lock per anchor store, weak-keyed so a collected store does not leak its
# lock. Same shape and same reason as projection's: the span below reads the
# known anchors, asks a model, and writes — so two concurrent observations
# mentioning the same new anchor can each be told "this is new" and each mint
# one, duplicating identity in the layer whose job is to unify it.
_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _lock_for(anchor_store: AnchorStore) -> asyncio.Lock:
    lock = _LOCKS.get(anchor_store)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[anchor_store] = lock
    return lock


# How many anchors one call may create. A day involves a handful of things;
# a caller passing raw calendar titles, or a test harness passing junk, would
# otherwise grow the taxonomy without bound and every new anchor is a node the
# gate must later reason about. Arithmetic on a count, not a judgement about
# the names.
MAX_NEW_ANCHORS_PER_CALL = 8


async def resolve_anchors(
    names: list[str],
    anchor_store: AnchorStore,
    judge: Judge,
    *,
    max_new: int = MAX_NEW_ANCHORS_PER_CALL,
) -> list[str]:
    """Map extracted anchor names onto minted anchor identity (loop 1).

    Names arrive as the model wrote them: "gym", "the gym", "gym session" are
    one anchor and three strings. Deciding that is a judgement about meaning,
    so it goes to a model — comparing or normalising the strings is precisely
    what CLAUDE.md bans, and the measured failure it cites (Jaccard conflating
    `Work Window` with `Deep Work Block Duration`) is this exact operation
    done wrong.

    Returns the anchor uids, minting one for every name the judge says is new.
    """
    if not names:
        return []

    async with _lock_for(anchor_store):
        candidates = anchor_store.all()
        known = {a.uid for a in candidates}
        resolved = await judge.resolve_anchors(names, candidates)

        by_name = {r.name: r.anchor_uid for r in resolved.resolutions}
        uids: list[str] = []
        minted = 0
        for name in names:
            uid = by_name.get(name)
            if uid is not None:
                # The id came from the model. Verify it names an anchor we
                # actually minted before attaching a rule to it: an invented
                # uid would attach the rule to nothing, and a walk that
                # traverses nothing is indistinguishable from a rule that
                # legitimately does not apply on this day.
                if uid not in known:
                    raise ValueError(
                        f"judge returned unknown anchor_uid {uid!r} for name "
                        f"{name!r}; not among {len(known)} candidates"
                    )
                uids.append(uid)
                continue
            minted += 1
            if minted > max_new:
                raise ValueError(
                    f"refusing to mint more than {max_new} new anchors in one "
                    f"call ({len(names)} names given, {minted - 1} already "
                    f"created); a call this unfamiliar is more likely raw text "
                    f"than a day's activities, and every anchor is permanent"
                )
            anchor = Anchor(name=name)
            anchor_store.upsert(anchor)
            uids.append(anchor.uid)
            # Later names in this batch may refer to the anchor just minted;
            # the judge saw them together and answered for all of them at once,
            # which is why this is one question rather than one per name.
            known.add(anchor.uid)

        return uids

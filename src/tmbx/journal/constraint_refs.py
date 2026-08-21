"""Extract journal-ready constraint references.

Duck-typed on purpose: this module must not import ``fateforger``.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from .models import ConstraintRef


def constraint_refs(objects: Iterable[Any]) -> list[ConstraintRef]:
    """Build ``ConstraintRef`` rows from constraint-like objects.

    A constraint carrying a minted ``hints["uid"]`` produces a ref tagged
    ``uid_kind="minted"``. A constraint without one produces a ref tagged
    ``uid_kind="unresolvable"`` with an empty ``uid`` — there is no
    content-derived fallback. Hashing a constraint's name/description/
    necessity/scope to invent an identity key is banned outright (CLAUDE.md):
    it decides whether two constraints mean the same thing from their text,
    and on this project's own data that mechanism conflated ``Work Window``
    with ``Deep Work Block Duration``. An honest "unresolvable" beats a
    plausible-looking guess.
    """
    refs: list[ConstraintRef] = []
    for obj in objects or []:
        hints = getattr(obj, "hints", None)
        hints = hints if isinstance(hints, dict) else {}

        minted = str(hints.get("uid") or "").strip()
        uid: str
        kind: Literal["minted", "unresolvable"]
        if minted:
            uid, kind = minted, "minted"
        else:
            uid, kind = "", "unresolvable"

        reason = hints.get("extraction_reason")
        refs.append(
            ConstraintRef(
                uid=uid,
                uid_kind=kind,
                reason=str(reason) if reason else None,
            )
        )
    return refs


__all__ = ["constraint_refs"]

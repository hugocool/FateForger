"""Extract journal-ready constraint references.

Duck-typed on purpose: this module must not import ``fateforger``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from .models import ConstraintRef


def _plain(value: Any) -> str:
    """Normalise enums, None, and scalars to a plain string."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def derived_uid(obj: Any) -> str:
    """Content-derived fallback key, mirroring the legacy signature.

    Unstable by construction: editing the constraint's text changes the key.
    Callers must tag results produced this way so the instability is visible.
    """
    signature = "|".join(
        [
            _plain(getattr(obj, "name", "")),
            _plain(getattr(obj, "description", "")),
            _plain(getattr(obj, "necessity", "")),
            _plain(getattr(obj, "scope", "")),
        ]
    )
    return "d:" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]


def constraint_refs(objects: Iterable[Any]) -> list[ConstraintRef]:
    """Build ``ConstraintRef`` rows from constraint-like objects."""
    refs: list[ConstraintRef] = []
    for obj in objects or []:
        hints = getattr(obj, "hints", None)
        hints = hints if isinstance(hints, dict) else {}

        minted = str(hints.get("uid") or "").strip()
        if minted:
            uid, kind = minted, "minted"
        else:
            uid, kind = derived_uid(obj), "derived"

        reason = hints.get("extraction_reason")
        refs.append(
            ConstraintRef(
                uid=uid,
                uid_kind=kind,  # type: ignore[arg-type]
                reason=str(reason) if reason else None,
            )
        )
    return refs


__all__ = ["constraint_refs", "derived_uid"]

# src/memory/anchor.py
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from memory.identity import mint_uid


class EdgeKind(str, Enum):
    """The two relations the taxonomy carries.

    Kept distinct rather than collapsed to one "related" edge because they
    behave differently under traversal: IS_A is transitive for inheriting
    rules (a rule about sport applies to hockey), PART_OF is not always
    (a rule about the evening ritual need not apply to dinner alone). The
    walk currently follows both; separating them is what makes it possible
    to stop.
    """

    IS_A = "is_a"
    PART_OF = "part_of"


class Anchor(BaseModel):
    """A recurring kind of thing the user's rules attach to.

    Identity is minted here (I3) and never derived from the name. Two
    observations saying "gym" and "the gym" resolve to one anchor because a
    model judged them the same, not because the strings normalise to each
    other — that judgement is exactly what CLAUDE.md reserves for a model.
    """

    uid: str = Field(default_factory=mint_uid)
    name: str

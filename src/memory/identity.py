# src/memory/identity.py
from __future__ import annotations

import uuid


def mint_uid() -> str:
    """Mint an opaque identity.

    I3: identity is never derived from content. Editing an observation's text
    must not change its uid, and two observations with identical content must
    receive different uids.
    """
    return uuid.uuid4().hex


#: `uuid4().hex` is exactly this: 32 characters drawn from lowercase hex.
_MINTED_UID_LENGTH = 32
_MINTED_UID_ALPHABET = frozenset("0123456789abcdef")


def is_minted_uid(value: str) -> bool:
    """Whether `value` has the shape `mint_uid` issues.

    Not a judgement about anything a person wrote: this compares an identifier
    against the format this system itself mints, which CLAUDE.md names as the
    documented exception to the no-matching rule. It is the same kind of check
    as verifying a uid the judge returned names a row we actually stored.

    A shape check cannot prove we issued this particular id, and it is not
    meant to. What it stops is the class of failure that reached production on
    2026-09-03: a caller passing the ids a fixture mints (`uid-1`) into the
    append-only log, where the observation is permanent and projects into a
    rule nobody stated.
    """
    return (
        len(value) == _MINTED_UID_LENGTH
        and not (set(value) - _MINTED_UID_ALPHABET)
    )

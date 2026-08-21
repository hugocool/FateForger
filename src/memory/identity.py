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

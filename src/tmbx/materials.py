# src/tmbx/materials.py
"""Handles for the things a block can point at.

A material is a foreign object — today a Notion ticket — that a block can
carry a link to. This module mints the handle and stores the row; nothing
here reads or judges what the user wrote.
"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .journal.models import Material

LINK_ID_PREFIX = "m"
LINK_ID_HEX_LEN = 10


def mint_link_id(source: str, external_id: str) -> str:
    """Mint the stable handle for one foreign object.

    Deterministic, so the same ticket keeps one handle across turns and days
    and ``MaterialStore.put`` is idempotent. Short, so a model can copy it into
    a patch without transcription error. Prefixed, so it is recognisably a
    handle and not a page id.

    Hashing here is not the content-derived identity CLAUDE.md bans: the input
    is an identifier Notion minted plus the name of the system that minted it.
    Nothing the user wrote — no title, no label — enters it, so two distinct
    tickets can never be conflated by reading alike.

    Both parts are required. An empty one would fold every material of that
    source onto one handle, silently, which is the failure mode worth being
    loud about.
    """
    if not source:
        raise ValueError("mint_link_id needs a source")
    if not external_id:
        raise ValueError("mint_link_id needs an external_id")
    digest = sha256(f"{source}:{external_id}".encode("utf-8")).hexdigest()
    return f"{LINK_ID_PREFIX}{digest[:LINK_ID_HEX_LEN]}"


class MaterialStore:
    """Reader/writer over the ``materials`` table.

    Takes a sessionmaker rather than a path, mirroring ``JournalStore``: the
    schema must already exist, and only ``init_journal`` creates it. Repo
    policy forbids runtime table creation in live paths, and building the
    sessionmaker is somebody else's synchronous constructor's job.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def put(
        self, *, source: str, external_id: str, url: str, label: str
    ) -> str:
        """Record one material and return its handle.

        Idempotent on ``(source, external_id)``. A second call updates ``url``
        and ``label`` — a ticket that was renamed or moved is still the same
        ticket — and leaves ``first_seen`` as it was.
        """
        link_id = mint_link_id(source, external_id)
        async with self._sessionmaker() as session:
            existing = await session.get(Material, link_id)
            if existing is None:
                session.add(
                    Material(
                        link_id=link_id,
                        source=source,
                        external_id=external_id,
                        url=url,
                        label=label,
                    )
                )
            else:
                existing.url = url
                existing.label = label
            await session.commit()
        return link_id

    async def get(self, link_id: str) -> Material | None:
        """Fetch one material by handle.

        ``None`` for a handle nothing has stored is an answer, not a failure:
        a link can outlive the row it pointed at, and the caller decides what
        an absent material means.
        """
        async with self._sessionmaker() as session:
            return await session.get(Material, link_id)

    async def get_many(self, link_ids: Sequence[str]) -> dict[str, Material]:
        """Fetch several materials in one query, keyed by handle.

        Handles nothing has stored are simply absent from the result. The
        caller is usually rendering a day's blocks, and one dead link must not
        cost the rest of them.
        """
        wanted = list(dict.fromkeys(link_ids))
        if not wanted:
            return {}
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Material).where(Material.link_id.in_(wanted))
            )
            return {row.link_id: row for row in result.scalars().all()}


__all__ = [
    "LINK_ID_HEX_LEN",
    "LINK_ID_PREFIX",
    "MaterialStore",
    "mint_link_id",
]

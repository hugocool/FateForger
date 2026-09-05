"""A write id this system did not mint must not reach the corpus (#288).

On 2026-09-03 a headless preview run called `memory_observe` with the ids a
test fixture mints -- `uid="uid-1"`, `session_id="session-1"` -- and the
observation was stored in Hugo's production store, where it projected into a
durable rule.

`write_uid` exists so a retry is a no-op (#168) and that stays. What was
missing is that the log accepted an identity it did not issue. Checking the
shape of an id *this system mints* is string work on a system-minted
identifier, which CLAUDE.md names as the exception to the no-matching rule --
nothing here reads what the user wrote.

The check lives at the MCP tool, not on `MemoryService.observe`, because the
tool is the trust boundary. In-process callers mint deterministic keys of
their own -- `promote_kind` passes `promotion-{slug}` over an already
validated slug -- and that is the system minting its own identity, which is
the allowed case. What arrives over the tool comes from outside.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.identity import is_minted_uid, mint_uid
from memory.judge import StubJudge
from memory.mcp_server import build_server
from memory.models import Channel, Tier
from memory.service import MemoryService

WHEN = datetime(2026, 9, 3, 10, 30, tzinfo=timezone.utc)
TEXT = "Move deep work block 30 minutes earlier and add a 15-minute buffer after it"


def _service(tmp_path) -> MemoryService:
    return MemoryService(str(tmp_path / "m.db"), StubJudge())


def test_a_minted_uid_is_recognised() -> None:
    assert is_minted_uid(mint_uid()) is True


@pytest.mark.parametrize(
    "value",
    [
        "uid-1",           # the fixture id that reached production
        "session-1",
        "",
        "not-hex-" + "0" * 24,
        mint_uid()[:31],   # right alphabet, wrong length
        mint_uid().upper(),  # uuid4().hex is lowercase; ours never is not
        mint_uid() + "0",
    ],
)
def test_an_id_this_system_did_not_mint_is_not_recognised(value: str) -> None:
    assert is_minted_uid(value) is False


async def test_the_observe_tool_refuses_a_write_uid_it_did_not_mint(tmp_path) -> None:
    """The 2026-09-03 call, refused at the door.

    Loudly: the store is append-only, so a placeholder that gets in is
    permanent, and a silent drop would be indistinguishable from a user who
    said nothing.
    """
    service = _service(tmp_path)
    server = build_server(service)

    with pytest.raises(Exception, match="uid-1"):
        await server.call_tool(
            "memory_observe",
            {
                "text": TEXT,
                "session_id": "session-1",
                "observed_at": WHEN.isoformat(),
                "write_uid": "uid-1",
            },
        )

    assert service._observations.all() == []


async def test_an_in_process_caller_may_mint_its_own_deterministic_key(tmp_path) -> None:
    """`promote_kind` does exactly this, and must keep working.

    Its key is derived from a slug the server already validated, so it is the
    system minting its own identity rather than accepting a foreign one.
    """
    service = _service(tmp_path)

    outcome = await service.observe(
        TEXT,
        channel=Channel.REVIEW,
        session_id="promotion:planning",
        observed_at=WHEN,
        write_uid="promotion-planning",
    )

    assert outcome.stored


async def test_a_minted_write_uid_still_works(tmp_path) -> None:
    """The retry contract (#168) is untouched."""
    service = _service(tmp_path)
    key = mint_uid()

    first = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN, write_uid=key
    )
    second = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN, write_uid=key
    )

    assert first.stored and second.stored
    assert len(service._observations.all()) == 1


async def test_no_write_uid_is_still_allowed(tmp_path) -> None:
    """Omitting it mints one here; only a *supplied* foreign id is refused."""
    service = _service(tmp_path)

    outcome = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    assert outcome.stored
    [stored] = service._observations.all()
    assert is_minted_uid(stored.uid)

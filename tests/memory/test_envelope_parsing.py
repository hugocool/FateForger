# tests/memory/test_envelope_parsing.py
"""Judge replies arrive in different envelopes depending on the transport.

OpenRouter accepts `response_format: {"type": "json_object"}` and returns bare
JSON. **MCP sampling has no equivalent field** — the protocol offers no way to
constrain output format — so models fence their answers. Gemini and Claude
both do; of four models measured over the sampling path, only one returned
bare JSON.

That makes this a weaker contract rather than incidental noise, which is why
it is parsed here and not stripped by a host. A host removing fences on the
server's behalf would hide a protocol difference and leave this parser working
only under that host.

Found by the harness session while proving MCP sampling against the real
server: three runs sampled correctly and failed anyway. `_ask`'s own comment
predicted a fenced code block as "a live possibility" while the code handled
only trailing junk.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance
from memory.prompts import PromptJudge

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def await_sync(coro):
    """Run one coroutine from a synchronous test, owning the loop.

    See tests/memory/test_judge.py for why this is `asyncio.run` and not
    `asyncio.get_event_loop()`.
    """
    import asyncio

    return asyncio.run(coro)


class _Replies(PromptJudge):
    """A judge that returns a canned envelope, to test the parser alone."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def complete(self, system: str, user: str) -> str:
        return self.reply


@pytest.mark.parametrize(
    "label,reply",
    [
        ("bare", '{"is_binding": true}'),
        ("fenced with language", '```json\n{"is_binding": true}\n```'),
        ("fenced plain", '```\n{"is_binding": true}\n```'),
        ("preamble sentence", 'Here is the answer:\n{"is_binding": true}'),
        ("fence and trailing prose", '```json\n{"is_binding": true}\n```\nHope that helps!'),
        ("stray trailing apostrophe", '{"is_binding": true}\''),
        ("brace in the preamble", 'The set {a,b} matters. {"is_binding": true}'),
        ("json array before the object", '[1,2,3]\n{"is_binding": true}'),
    ],
)
async def test_the_object_is_found_whatever_surrounds_it(label, reply):
    assert await _Replies(reply)._ask("system", "user") == {"is_binding": True}


async def test_a_reply_with_no_object_still_raises():
    """Tolerating noise around an answer must never become tolerating a
    missing answer — that is the difference between a loose parser and one
    that invents a judgement."""
    with pytest.raises(ValueError, match="could not parse"):
        await _Replies("I cannot answer that.")._ask("system", "user")


async def test_a_bare_array_is_not_accepted_as_a_judgement():
    """Every judgement is an object. An array parses as valid JSON and is not
    an answer to any question this asks."""
    with pytest.raises(ValueError, match="could not parse"):
        await _Replies("[1, 2, 3]")._ask("system", "user")


async def test_truncation_is_not_silently_half_read():
    """A cut-off reply must raise rather than yield whatever parsed.

    The sampling path can truncate at max_tokens, and a partial object is the
    shape most likely to look like a plausible judgement.
    """
    with pytest.raises(ValueError, match="could not parse"):
        await _Replies('{"is_binding": true, "rationale": "because')._ask(
            "system", "user"
        )


def test_requires_block_accepts_a_listed_slug_or_null():
    from memory.judge import RequiresBlockJudgement

    assert await_sync(
        _Replies('{"slug": "planning", "rationale": "states a session must exist"}')
        .requires_block(_obs("x"), ["planning", "sleep"])
    ) == RequiresBlockJudgement(slug="planning", rationale="states a session must exist")
    assert await_sync(
        _Replies('{"slug": null, "rationale": "a duration cap"}').requires_block(_obs("x"), ["planning"])
    ).slug is None


def test_requires_block_refuses_a_slug_that_was_not_offered():
    """Named, and carrying the code spec §5 gives it.

    The MCP tool surfaces the message as-is, so the code is what a host can key
    an error path off without reading English -- and UnknownKind is still a
    ValueError, so every caller that already handled one keeps working.
    """
    from memory.judge import UnknownKind

    with pytest.raises(UnknownKind) as excinfo:
        await_sync(_Replies('{"slug": "plan-review"}').requires_block(_obs("x"), ["planning"]))
    assert UnknownKind.code == "unknown_kind"
    assert f"[{UnknownKind.code}]" in str(excinfo.value)


def test_requires_block_asks_nothing_when_no_kinds_are_registered():
    class _Explodes(_Replies):
        async def complete(self, system: str, user: str) -> str:
            raise AssertionError("must not call the model with an empty registry")

    assert await_sync(_Explodes("").requires_block(_obs("x"), [])).slug is None


def test_tier_refuses_a_day_type_outside_the_minted_vocabulary():
    """The tier judgement's day_types is a closed vocabulary this system minted,
    and the transport verifies it exactly as requires_block verifies its slug.

    Without this a paraphrase -- "workday" for "working" -- is stored, and the
    read path filters by equality over these words, so the rule silently applies
    on no day at all. Set membership over minted words, not a judgement about
    meaning.
    """
    with pytest.raises(ValueError) as excinfo:
        await_sync(
            _Replies('{"tier": "durable", "label": "x", "day_types": ["workday"]}').tier(
                _obs("x")
            )
        )
    assert "workday" in str(excinfo.value)


def test_tier_accepts_a_day_type_from_the_vocabulary():
    judgement = await_sync(
        _Replies('{"tier": "durable", "label": "x", "day_types": ["working"]}').tier(
            _obs("x")
        )
    )
    assert judgement.day_types == ["working"]

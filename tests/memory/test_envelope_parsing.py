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

import pytest

from memory.prompts import PromptJudge


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

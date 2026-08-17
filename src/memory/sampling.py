# src/memory/sampling.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from memory.prompts import PromptJudge


class SamplingUnavailable(RuntimeError):
    """The host cannot answer model questions at all.

    Raised when a client connects without declaring the sampling capability,
    or when no request is in flight to borrow a model from. This is a
    configuration fault, not a transient: every write into memory will fail
    the same way until the host is fixed.

    It is deliberately fatal to the call. The alternative — degrading to
    "extracted nothing" — makes a misconfigured host indistinguishable from a
    user who said nothing memorable, and the corpus stops growing with no
    symptom anyone would notice.
    """


class SamplingDeclined(RuntimeError):
    """The host refused this particular sampling request.

    Distinct from unavailable: the capability exists and the next request may
    well succeed. A user rejecting a sampling prompt lands here. Still raised
    rather than swallowed, for the same reason — the caller is entitled to
    know its statement was not recorded.
    """


@runtime_checkable
class Sampler(Protocol):
    """Somewhere to send a question when this package does not own a model.

    The whole point of the port: whoever hosts the memory server decides
    which model answers. An MCP host supplies one backed by
    `sampling/createMessage`; an in-process host binding the service directly
    supplies one backed by whatever client it already has. Neither needs an
    API key here.
    """

    async def complete(self, system: str, user: str) -> str: ...


class SamplingJudge(PromptJudge):
    """Judge that borrows the host's model instead of owning one.

    The five questions are inherited unchanged from PromptJudge, so a
    judgement made through a host is the same judgement made through
    OpenRouter — only the model differs, and that choice now sits with
    whoever is driving.

    Nothing here catches SamplingUnavailable or SamplingDeclined. The write
    path treats them like any other judge failure: loudly.
    """

    def __init__(self, sampler: Sampler) -> None:
        self._sampler = sampler

    async def complete(self, system: str, user: str) -> str:
        return await self._sampler.complete(system, user)

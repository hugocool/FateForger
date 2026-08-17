# src/memory/openrouter_judge.py
from __future__ import annotations

import asyncio
import os

import httpx

from memory.prompts import PromptJudge

# Backoff between retry attempts (seconds), one entry per retry gap. A
# module-level tuple so tests can shrink it instead of sleeping for real.
_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)


class _ProviderError(Exception):
    """OpenRouter returned HTTP 200 with a body that has no `choices` key.

    This is how OpenRouter surfaces an upstream provider hiccup: the
    envelope is well-formed enough to pass `raise_for_status()`, but the
    body is `{"error": {...}}` instead of a completion. Treated as a
    transient transport failure, not a semantic one — the request itself
    didn't get an answer, so it's eligible for retry.
    """


class OpenRouterJudge(PromptJudge):
    """Judge backed directly by OpenRouter.

    One of two transports for the same five questions; the questions
    themselves live in `prompts.PromptJudge`. Use this where there is no host
    to borrow a model from — offline corpus passes, backfill, eval tests. A
    server hosted by an agent should use SamplingJudge instead, so the model
    choice belongs to whoever is driving.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "google/gemini-3.6-flash",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        # A model call can comfortably exceed httpx's 5s default read timeout,
        # and a corpus run makes hundreds of sequential calls — one slow
        # response must not kill the run. Applies only to the client we
        # construct; an injected client keeps its caller's configuration.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0)
        )
        # Only close what we created; an injected client belongs to its caller.
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the HTTP client, but only if this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenRouterJudge:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def complete(self, system: str, user: str) -> str:
        # Transient transport failures are retried; semantic failures are not.
        # A ~500-call corpus run has died three separate times on three
        # different transients (read timeout, provider error-in-200-body,
        # trailing junk). Retrying the SAME question substitutes nothing and
        # is not the silent fallback CLAUDE.md bans — a silent fallback swaps
        # in a *different* answer when the real one is missing; a retry asks
        # the identical question again and, if every attempt fails, still
        # raises loudly at the end — now with the provider's actual error
        # visible instead of a bare KeyError.
        #
        # Parsing deliberately happens in PromptJudge._ask, OUTSIDE this loop:
        # malformed content is a semantic failure of a well-formed envelope,
        # not a transient, and must raise immediately rather than burn retries
        # on an answer that will never change.
        last_error: Exception | None = None
        attempts = len(_RETRY_DELAYS) + 1
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        # Extraction is term typing, not deliberation, so
                        # reasoning is held to the floor. Note: this endpoint
                        # REJECTS {"enabled": False} with "Reasoning is
                        # mandatory for this endpoint and cannot be disabled"
                        # — verified against the live API on 2026-08-16.
                        # "minimal" is the lowest accepted setting.
                        "reasoning": {"effort": "minimal"},
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                body = response.json()
                if "choices" not in body:
                    # HTTP 200 with an error body: OpenRouter's way of
                    # surfacing an upstream provider hiccup. raise_for_status
                    # already passed, so without this check the next line
                    # would raise a bare KeyError that hides the provider's
                    # own error message.
                    raise _ProviderError(
                        f"provider returned 200 without choices: "
                        f"{body.get('error', body)!r}"
                    )
                return body["choices"][0]["message"]["content"]
            except (httpx.TimeoutException, _ProviderError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    # A 4xx other than "too many requests" will not get
                    # better on retry — it's a bad request, not a transient.
                    raise
                last_error = exc

            if attempt == attempts - 1:
                raise ValueError(
                    f"judge request failed after {attempts} attempts: {last_error}"
                ) from last_error
            await asyncio.sleep(_RETRY_DELAYS[attempt])

        raise AssertionError("unreachable: the loop only exits via return or raise")


def openrouter_judge_from_env() -> OpenRouterJudge:
    """Build the real judge from environment configuration.

    Raises rather than defaulting when the key is absent: a memory server
    that silently cannot judge is worse than one that refuses to start.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenRouterJudge(
        api_key=api_key,
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )

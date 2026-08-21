# src/memory/mcp_server.py
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Callable

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.session import ServerSession
from mcp.shared.exceptions import McpError

from memory.models import Channel
from memory.sampling import SamplingDeclined, SamplingJudge, SamplingUnavailable
from memory.service import MemoryService

INSTRUCTIONS = """\
Durable memory for a personal scheduling agent.

memory_observe records something the user said; the server decides what it
means (which anchors, durable rule or today's fact, interaction chatter,
restatement of a known rule) and files it. It needs the sampling capability:
the server has no model of its own and asks yours.

memory_get_active_constraints returns the durable rules applying on a day.
It never samples — structural filtering only, so it is fast, repeatable, and
safe to call inside a planning loop. Pass anchor_uids from
memory_resolve_anchors to get only the rules bearing on what the day contains;
without them you get every applicable rule. memory_get_faded_constraints returns the rules
withheld because nothing has re-stated them lately; they are candidates for
review, not for planning.

memory_reproject re-derives stored constraints from the observations behind
them. Constraints keep the fields the build that created them produced, so
this is how an improvement in judgement reaches rules that already exist. It
samples once per observation; run it deliberately, not in a loop.\
"""

# Judgements are small JSON objects with a short rationale. Generous enough
# that no answer is truncated, small enough that a runaway reply stops early —
# truncation would arrive as unparseable JSON rather than a wrong judgement.
MAX_TOKENS = 1024


class McpSampler:
    """Borrows the connected host's model via `sampling/createMessage`.

    Holds no API key and names no model: whoever drives this server decides
    what answers. The session is resolved per call rather than stored, because
    it belongs to the request in flight — one server instance serves many.
    """

    def __init__(
        self,
        session_provider: Callable[[], ServerSession],
        *,
        max_tokens: int = MAX_TOKENS,
        temperature: float | None = None,
    ) -> None:
        self._session_provider = session_provider
        self._max_tokens = max_tokens
        # Unpinned by default, and this was measured rather than assumed.
        # Pinning to 0 was once justified as making the write path
        # reproducible; two identical passes over the real corpus showed it
        # buys nothing here — no field disagreed less at 0, and whole-record
        # disagreement was higher. Plausibly the endpoint still samples
        # reasoning tokens, which this API refuses to disable outright.
        #
        # A default that looks like a guarantee and is not one is worse than
        # no default: it invites callers to skip resampling. Determinism here
        # comes from comparing categorical fields, not from a parameter.
        # See docs/superpowers/research/2026-08-20-sampler-noise-floor.md.
        #
        # A host may still pass one, and remains free to ignore it — the MCP
        # spec makes every sampling parameter advisory.
        self._temperature = temperature

    async def complete(self, system: str, user: str) -> str:
        try:
            session = self._session_provider()
        except ValueError as exc:
            # No request in flight. Reached only by calling the write path
            # outside a tool invocation — a wiring error, and a loud one.
            raise SamplingUnavailable(
                "no MCP request in flight, so there is no host session to "
                "sample from; memory_observe must be driven by a tool call"
            ) from exc

        params = session.client_params
        capabilities = params.capabilities if params else None
        if capabilities is None or capabilities.sampling is None:
            client = params.clientInfo.name if params and params.clientInfo else "unknown"
            raise SamplingUnavailable(
                f"host {client!r} did not declare the sampling capability; "
                f"this server has no model of its own, so nothing can be "
                f"recorded until the host enables sampling"
            )

        try:
            result = await session.create_message(
                messages=[
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text=user)
                    )
                ],
                system_prompt=system,
                max_tokens=self._max_tokens,
                **({} if self._temperature is None else {"temperature": self._temperature}),
            )
        except McpError as exc:
            # The host answered the request with an error — a user rejecting
            # the sampling prompt lands here. Recoverable in principle, so it
            # is a distinct type, but still raised: the caller is entitled to
            # know its statement was not recorded.
            raise SamplingDeclined(f"host declined the sampling request: {exc}") from exc

        if result.stopReason == "maxTokens":
            raise ValueError(
                f"host truncated the reply at {self._max_tokens} tokens; the "
                f"judgement is incomplete and must not be parsed"
            )
        if result.content.type != "text":
            raise ValueError(
                f"host returned {result.content.type!r} content for a question "
                f"that has only a textual answer"
            )
        return result.content.text


def register_tools(mcp: FastMCP, service: MemoryService) -> None:
    """Bind a service's verbs onto an MCP server."""

    @mcp.tool(name="memory_observe")
    async def memory_observe(
        text: str,
        session_id: str,
        channel: str = "planning",
        observed_at: str | None = None,
        write_uid: str | None = None,
    ) -> dict:
        """Record one user statement and file it into memory.

        `session_id` scopes duplicate detection: pass the same value for every
        statement in one conversation, and a fresh value per conversation. A
        value that changes per call makes dedup a no-op; a constant value
        makes the candidate list grow without bound.

        `write_uid` makes the call safe to retry. Without it, retrying appends
        a second observation indistinguishable from the user having said the
        same thing twice — and this log is append-only, so it is permanent.
        Evidence is what promotion and decay count, so a retry loop inflates
        support for whatever failed most often. Generate one id per statement
        you intend to record, reuse it across retries of that statement, and
        never reuse it for a different statement.
        """
        when = (
            datetime.fromisoformat(observed_at)
            if observed_at
            else datetime.now(timezone.utc)
        )
        outcome = await service.observe(
            text,
            channel=Channel(channel),
            session_id=session_id,
            observed_at=when,
            write_uid=write_uid,
        )
        return outcome.model_dump(mode="json")

    @mcp.tool(name="memory_reproject")
    async def memory_reproject(constraint_uid: str | None = None) -> dict:
        """Re-derive stored constraints from their source observations.

        Run this after the server's judgement of what statements mean has
        improved. Constraints created earlier do not otherwise acquire the
        improvement: a restatement folds into the existing row and refreshes
        only its timestamp, so an old rule keeps whatever fields the build
        that created it produced.

        Samples once per source observation, so it is slow and costs the
        host's tokens — not something to call inside a planning loop. Pass
        `constraint_uid` to re-derive a single rule. Identity is preserved and
        the returned report names every field that moved.
        """
        report = await service.reproject(constraint_uid)
        return report.model_dump(mode="json")

    @mcp.tool(name="memory_resolve_anchors")
    async def memory_resolve_anchors(names: list[str]) -> list[str]:
        """Anchor uids for the things a day involves, from their names.

        Call this once when you know the day's events — pass the activities
        they denote ("hockey", "school run") — then hold the uids and pass
        them to memory_get_active_constraints as often as you like.

        Separate from the read call because deciding that "Hockey practice"
        means the hockey anchor needs a model, and the read path must not have
        one: it sits inside a planning loop, and the same day read twice has
        to answer the same way. Samples once. New anchors are created.
        """
        return await service.resolve_anchor_names(names)

    @mcp.tool(name="memory_split_constraint")
    async def memory_split_constraint(
        constraint_uid: str, observation_uids: list[str]
    ) -> dict:
        """Separate observations wrongly folded into one rule.

        Use when one constraint is visibly two rules — a rule about a part
        merged into the rule about the whole containing it, or two unrelated
        statements sharing vocabulary. Read the constraint's provenance first
        and name the observations that should become their own rule.

        You decide what belongs apart; the server does not re-judge it. Both
        halves are re-derived from the observations they end up with, the
        original keeps its id, and the observation log is untouched.
        """
        original, newborn = await service.split_constraint(
            constraint_uid, observation_uids
        )
        return {"original_uid": original, "new_uid": newborn}

    @mcp.tool(name="memory_get_active_constraints")
    def memory_get_active_constraints(
        day: str,
        stage: str | None = None,
        anchor_uids: list[str] | None = None,
        day_type: str | None = None,
    ) -> list[dict]:
        """Durable rules applying on `day` (YYYY-MM-DD). No model call.

        Pass `anchor_uids` from memory_resolve_anchors to get only the rules
        bearing on what the day actually contains, rather than every standing
        rule. Rules carrying no anchors are always included — those are about
        the shape of the day rather than a thing in it.

        **Pass `day_type` whenever you know it.** Most rules here are working-day
        rules, and weekday is not a reliable stand-in for a working day: without
        this, a day the user is on holiday returns their entire working week —
        commute duration, deep-work entry gates, no-meetings-before-13:00. One of
        "working", "vacation", "holiday", "sick", "weekend". Omitting it is safe
        but broad. If your host supports sampling, memory_classify_day derives it
        from calendar event titles; if it does not, classify the day yourself and
        pass the string — that judgement has to happen somewhere, and this server
        cannot make it without a model.
        """
        views = service.get_active_constraints(
            date.fromisoformat(day), stage, anchor_uids, day_type=day_type
        )
        return [v.model_dump(mode="json") for v in views]

    @mcp.tool(name="memory_get_suspended_constraints")
    def memory_get_suspended_constraints(
        day: str, day_type: str | None = None
    ) -> list[dict]:
        """Rules that are true, and deliberately not in force on `day`. No model call.

        Render these; do not plan against them. Absence from the active list has
        three causes that look identical — no such rule, a rule that does not
        apply today, a rule that has gone stale — and they need opposite
        responses. This separates the second out, so a planner can say "21
        working-day rules are suspended, today is vacation" rather than quietly
        returning a shorter list and looking like it forgot.
        """
        views = service.get_suspended_constraints(
            date.fromisoformat(day), day_type=day_type
        )
        return [v.model_dump(mode="json") for v in views]

    @mcp.tool(name="memory_classify_day")
    async def memory_classify_day(events: list[str]) -> str:
        """What kind of day this is, from calendar event titles. Samples once.

        Feed the result to memory_get_active_constraints as `day_type`. This
        needs the sampling capability; a host without it gets a loud failure
        rather than a wrong answer, and should classify the day itself.
        """
        return await service.classify_day(events)

    @mcp.tool(name="memory_get_faded_constraints")
    def memory_get_faded_constraints(
        day: str, stage: str | None = None
    ) -> list[dict]:
        """Rules withheld on `day` because they have gone stale. No model call.

        Fading is how the server stops serving rules nobody has restated. That
        is silent deletion unless someone can see what was withheld — which is
        what this is for. Surface these for review; do not plan against them.
        """
        views = service.get_faded_constraints(date.fromisoformat(day), stage)
        return [v.model_dump(mode="json") for v in views]


def build_server(service: MemoryService) -> FastMCP:
    """The MCP face of an already-built MemoryService.

    A factory rather than module state so tests bind a stub judge and a tmp
    database, and so a host can run several isolated servers.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)
    register_tools(mcp, service)
    return mcp


def build_sampling_server(db_path: str) -> FastMCP:
    """A server that judges with the host's model rather than its own.

    The default, and the reason this package needs no API key: the server is
    built before the service so the sampler can resolve the live request's
    session on every call.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)
    sampler = McpSampler(lambda: mcp.get_context().session)
    register_tools(mcp, MemoryService(db_path, SamplingJudge(sampler)))
    return mcp


def main() -> None:
    db_path = os.environ.get("MEMORY_DB_PATH", "data/memory.db")
    build_sampling_server(db_path).run()  # stdio transport


if __name__ == "__main__":
    main()

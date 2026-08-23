"""Run the constraint-memory MCP server with a tool allow-list.

Why this exists, in one line: DSH's MCP bridge has no tool allow-list, so
without it every tool the memory server publishes reaches the model.

`@deepseek-ai/dsh-mcp-client`'s config schema is transport, serverName,
command, args, env, cwd, toolCallTimeoutMs, failOnStartupError, reconnect.*
and — since `feat/mcp-client-sampling` — sampling.*. There is still no
include, exclude, allow or deny.

The host now declares the sampling capability on this mount, so the argument
that used to do most of the filtering is gone: the three write tools that
sample no longer fail loudly, they work. That changes the answer for one tool
and not for the others.

`memory_observe` is now exposed. It is the write path this whole session
exists to exercise (#149): a planning session that records nothing produces
no corpus, and the journal side of the ticket cannot be tested with it
withheld.

Whether it writes to a throwaway copy or to the real corpus is decided
ENTIRELY by MEMORY_DB_PATH at launch, and nothing here enforces the safe
answer. This file said "it writes to a throwaway copy" as though that were a
property of the server; it is a property of one way of starting it, and the
process serving 127.0.0.1:8010 on 2026-08-22 was started against
data/memory.db -- Hugo's real preference corpus. It still was on 2026-08-23,
because scripts/demo.py hardcodes that path and every start has used it.

This file used to be called "memory-readonly-server.py", which named the
allow-list and was read as naming the store (#188). Three of the four
surviving tools read; this one writes, wherever it was pointed. A docstring
correcting that is read only by someone who already opened the file, and the
belief forms earlier than that -- in a profile row, in `ps` output, in a
directory listing. So the name changed too.

Check before assuming, because the failure is silent in the direction that
matters -- an unintended write is indistinguishable from a real preference.
`scripts/demo.py status` now reports it per process, read from the running
process rather than from its spec, which is the same read this once asked
people to do by hand and nobody did:

    ps eww <pid> | tr ' ' '\n' | grep MEMORY_DB_PATH

`memory_split_constraint` stays withheld, and the argument for withholding it
never depended on sampling. It does not sample, so it would simply have run
even under the old mount. Splitting is the inverse of a merge, merges are
currently irreversible, and it exists only because a repair once had to be
done in raw SQL — nothing in a planning loop should reach it.

`memory_classify_day` stays withheld on purpose rather than by inertia. It
samples, so it would now work; the day type is nevertheless the host's
judgement to make from what the user said, and the acceptance test measures
whether the model derives "vacation" from a sentence about a summer holiday.
A tool that answers it removes the thing being measured.

`memory_reproject`, `memory_resolve_anchors` and `memory_get_faded_constraints`
stay withheld: reproject rewrites stored rows wholesale (#154), and the other
two are maintenance reads with no role in a planning loop.

So the boundary is taken away rather than asked for. This builds exactly the
server the ordinary entry point builds and then removes the tools outside the
allow-list through FastMCP's public `remove_tool`. Nothing in `src/memory` is
imported-around, copied or modified: the tools that survive are the server's
own, carrying the server's own descriptions and schemas.

Drift is made loud in the direction that matters. An allow-listed tool that
has disappeared raises and boot fails, because silently losing the read path
looks identical to a store with nothing in it. A tool that is new and
unlisted is removed and named on stderr — the safe direction to be wrong in,
since a new *write* tool must never be reachable by default.

    PYTHONPATH=<memory>/src MEMORY_DB_PATH=<absolute> python memory-allowlisted-server.py
"""

import asyncio
import os
import sys

from memory.mcp_server import build_bridged_server, build_sampling_server

# Three reads and one write. Two answers about the day (who the user is in
# general, and what is deliberately not in force today), one about this
# conversation, and the one verb that puts something into it. Everything else
# is a maintenance operation or a judgement this host should be making itself.
ALLOWED = frozenset(
    {
        "memory_get_active_constraints",
        "memory_get_suspended_constraints",
        "memory_get_session_constraints",
        "memory_observe",
    }
)


def main() -> None:
    db_path = os.environ.get("MEMORY_DB_PATH")
    # memory.mcp_server.main() defaults this to the relative "data/memory.db",
    # which resolves against the child's cwd and yields a fresh empty store
    # rather than an error. An empty store is indistinguishable from a user
    # with no rules, so refuse anything but an absolute path.
    if not db_path or not os.path.isabs(db_path):
        raise SystemExit(
            f"MEMORY_DB_PATH must be set to an absolute path; got {db_path!r}. "
            f"A relative path silently opens an empty store."
        )

    # This harness declares no sampling capability, so a server built to ask
    # its host cannot judge at all and memory_observe raises on every call --
    # the read path works and nothing is ever recorded. MEMORY_JUDGE=openrouter
    # gives the server its own provider so the write path runs here.
    #
    # It is a compromise and should stay one: #150 removed the model so the
    # host's would govern quality. The case for taking it now is not
    # convenience -- every quality number this package has is measured against
    # google/gemini-3.6-flash, so this is the better-*measured* option, and
    # host sampling is the right architecture with quality nobody has looked at.
    #
    # Chosen at boot, never per call, and never inferred from a key merely
    # being present: a server that quietly stops asking its host because a
    # variable was set elsewhere keeps filling the store, just judged by
    # someone nobody chose.
    judge_kind = os.environ.get("MEMORY_JUDGE", "host")
    if judge_kind == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit(
                "MEMORY_JUDGE=openrouter needs OPENROUTER_API_KEY. Refusing to "
                "start rather than falling back to host sampling, which this "
                "harness cannot do -- the write path would fail on every call "
                "while the reads kept working."
            )
        server = build_bridged_server(
            db_path,
            api_key=api_key,
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
        )
    elif judge_kind == "host":
        server = build_sampling_server(db_path)
    else:
        raise SystemExit(
            f"MEMORY_JUDGE={judge_kind!r} is not a judge this build knows; "
            f"use 'host' or 'openrouter'."
        )

    # Public API on both sides: list_tools() is async, and boot is outside
    # any loop, so this is the one place asyncio.run is correct.
    published = {t.name for t in asyncio.run(server.list_tools())}

    missing = ALLOWED - published
    if missing:
        raise SystemExit(
            f"memory server no longer publishes {sorted(missing)}; the read "
            f"path this profile depends on is gone. Published: {sorted(published)}"
        )

    withheld = sorted(published - ALLOWED)
    for name in withheld:
        server.remove_tool(name)

    print(
        f"memory-allowlist: exposing {sorted(ALLOWED)}; withheld {withheld}",
        file=sys.stderr,
    )
    # Transport is chosen by the caller, not baked in. Under stdio the harness
    # spawns a fresh Python process per turn and pays its import cost every
    # time; under streamable-http the server is started once and connected to,
    # which is the difference between a warm reply and a cold boot.
    transport = os.environ.get("MEMORY_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        server.settings.host = os.environ.get("MEMORY_MCP_HOST", "127.0.0.1")
        server.settings.port = int(os.environ.get("MEMORY_MCP_PORT", "8010"))
        print(f"memory-allowlist: serving http on {server.settings.host}:"
              f"{server.settings.port}", file=sys.stderr, flush=True)
    server.run(transport=transport)


if __name__ == "__main__":
    main()

# Peer-session coordination: prior art for 49 sessions on one checkout

**Date:** 2026-09-07
**Question:** Is there a battle-tested, public coordination protocol or agent skill that already solves peer-session coordination for our shape — 49 live Claude Code sessions plus 2 cloud agents sharing one git checkout, GitHub Issues already the tracker, Codex a first-class second reader — and should we adopt it, adopt part of it, or author our own?

**Headline:** Three projects genuinely enforce something, and each one enforces a *different third* of the problem. Beads has the only real atomic compare-and-set claim (26.9k stars, 30 contributors). Concord has the only real cross-harness edit-collision block, delivered through a `PreToolUse` hook that also exists for Codex. mcp_agent_mail has the only real answer to a dead holder — TTL plus an inactivity sweeper that probes the filesystem and git rather than trusting a heartbeat. **No project has all three, and all three replace GitHub Issues with their own store.** Meanwhile the primitive our existing convention rests on — the GitHub assignee — is documented set-union with no conditional write, so it cannot arbitrate a race even in principle, and every session here assigns to the same account. The recommendation is **AUTHOR**, against a requirements list stolen almost entirely from the three above.

---

## 1. What the problem actually is

Grounding first, because two of the constraints turn out to eliminate most of the field.

**The claim already exists and is prose.** Wayfinder's rule is explicit: "A session **claims** a ticket by assigning it to the dev driving the map, **first**, before any work, so concurrent sessions skip it. That assignee _is_ the claim: an open, unassigned ticket is unclaimed" (`~/.claude/skills/wayfinder/SKILL.md:67`). Nothing enforces this. It is an instruction in a skill file that an agent may skip, forget, or never reach.

**It has already failed in production.** On 2026-09-05 a controller charted #316–#320 on map #157 and dispatched implementers without assigning the tickets. A second controller session read #319 as frontier, dispatched its own implementer into the *same worktree and branch*, and the two agents collided on one file. The loser stopped and wrote an additive report; the first controller found out from a ticket comment and an unexplained uncommitted diff (recorded in `wayfinder-claim-before-dispatch`).

**The GitHub assignee cannot arbitrate a race.** From the REST reference for `POST /repos/{owner}/{repo}/issues/{issue_number}/assignees`: "Users already assigned to an issue are not replaced." The operation is additive set-union, capped at 10 assignees, with no ETag, `If-Match`, or compare-and-set support anywhere on the endpoint ([GitHub REST: assignees](https://docs.github.com/en/rest/issues/assignees)). Two sessions racing to claim one issue **both succeed**. Worse, in our setup every session authenticates as `hugocool`, so the second session's add is a literal no-op that returns success and is indistinguishable from winning. The assignee is a durable, visible *record* of a claim. It is not a lock, and no amount of convention makes it one.

**Session names collide; session ids do not.** The auto-generated display names repeat (`admonish-1-8b` appeared three times against different refs) and carry no meaning. But each session already owns a stable unique identifier — its transcript UUID.

**A free liveness signal already exists.** Measured on this machine at 12:39 on 2026-09-07: 32 session transcripts across the main checkout and its six worktree project directories, of which **7 had been written in the last 15 minutes**. The transcript file's mtime is a harness-native, zero-cost liveness probe, updated on every turn, requiring no daemon, no heartbeat thread, and no cooperation from the session being probed. This matters enormously for the design, because it means the hardest part of a lease — knowing whether the holder is alive — is already solved for Claude Code sessions without writing any of it.

```
~/.claude/projects/-Users-hugoevers-VScode-projects-admonish-1*/<session-uuid>.jsonl
```

---

## 2. Concord (`Get-Concord-AI/concord-mcp`)

MIT. 315 stars, 14 forks, 4 contributors of whom one is dependabot. Created 2026-07-14, `v0.10.4` released 2026-09-05. A company project (`getconcord.ai`), not a hobby repo, but two months old and small.

The ticket's description of Concord came from an aggregator listing, which conflated it with an unrelated Apache-2.0 project of the same name (`Softsensor-org/concord`, 3 stars, 0 forks). The E2EE / signal-decay / quorum-voting claims belong to the aggregator's copy and appear nowhere in the source I read. What is actually in the repo is more modest and more useful.

**What it enforces.** Two things, and the distinction is drawn carefully in the code itself.

*Task ownership is enforced*, server-side in the tool handler:

```ts
// src/tools/claim-work.ts
if (existing?.agentId !== null && existing?.agentId !== undefined &&
    input.agent_id !== existing.agentId) {
  throw new Error(
    `Task ${input.task_id} is owned by ${existing.agentId}; use transfer_work to offer or reassign it.`);
}
```

A second agent provably loses. That is real arbitration.

*Scope overlap is not enforced.* `detectOverlaps` returns warnings, and the source is candid that this is point-in-time only: "an empty `overlaps` only means nothing conflicts *right now*. A later overlapping claim will not appear here." Claim breadth is likewise "a suggestion to split the work into smaller, independently-handoffable tasks — **never a rejection**."

**The genuinely interesting part is the hook.** `src/cli/commands/hook.ts` implements a `PreToolUse` decision that returns `block: true` (exit 2) when the file being edited is claimed by another active task:

```ts
return { block: true,
  message: `Concord: ${filePath} is claimed by another active task (${detail}). ` +
           `Coordinate or update your claim before editing.`,
  result: 'blocked', conflictingTaskCount: overlaps.length };
```

This is the only hook-side edit block I found in any candidate. It is gated on `CONCORD_TASK` being set in the environment: without it, the hook cannot distinguish your own claimed files from a real conflict, so it degrades to a warning rather than risk blocking an agent from editing its own files. That degradation is the right call and worth copying.

**Codex support is real, not aspirational.** `src/install/codex-config.ts` writes a fenced block into `~/.codex/config.toml` registering `[mcp_servers.concord]` and three `[[hooks.*]]` tables. The splice is line-range based specifically so unrelated tables and their comments survive. A code comment records a real difference between the harnesses: "Codex passes its session id on stdin rather than in the environment, so every hook reads the payload to work out which agent it is." Cursor, Gemini and Grok get equivalent adapters under `plugin/`.

**How a claim is released when a session dies — this is where it falls down.** Concord derives liveness rather than storing it, and the doc comment in `src/domain/presence.ts` states the principle exactly right:

> Liveness is *derived* from how long ago an agent was last seen — never stored. A registered claim is durable, but presence must decay so a crashed or walked-away agent stops looking active.

Thresholds are live < 5 min, idle < 30 min, away < 1 h, archived beyond (`DEFAULT_PRESENCE_THRESHOLDS`). An unparseable timestamp is treated as `archived`, "an agent whose last activity cannot be read must not sit on the roster forever" — a good failure direction.

But **nothing consults liveness to release a claim.** Grepping `src/tools/` and `src/db/repositories/` for `liveness`/`archived` returns exactly one hit, a comment on the agent roster. `handleClaimWork` throws on a foreign owner unconditionally, without asking whether that owner is alive. Recovery is manual: `reassign_task --force`, itself restricted to "the current owner or a registered agent for human owner". **A dead Concord agent's claim blocks the task forever until a human intervenes.** The presence model is exactly what we need and it is wired to the roster display instead of to the lock.

Notably, Concord's own Claude plugin binds `SessionStart`, `PostToolUse` and `Stop` — **not `SessionEnd`** (`plugin/concord-relay/hooks/hooks.json`). A project purpose-built for this problem does not rely on an end-of-session hook to release anything. That is a design signal, not an oversight.

**Second tracker: yes.** A SQLite database under `.concord/`, with its own `tasks`, `handoffs`, `reviews`, `task_updates` and `agents` tables (`src/db/schema.ts`). There is **no GitHub integration of any kind** — grepping the entire repo for "github issue", "gh issue" or "issue number" returns nothing. `task_id` is a free-form `TEXT PRIMARY KEY`, so it *could* hold `368`, but nothing syncs status, assignee, or closure back to GitHub. Adopting Concord means running two trackers and reconciling them by hand.

**Hosted dependency: no, but there is telemetry.** The relay is a local UNIX socket (`src/relay/server.ts`), not a cloud service. Telemetry does go to `getconcord.ai`: operation names, outcomes, durations, aggregate overlap/edit-guard results, task lifecycle transitions. The README states it never sends code, raw paths, remotes, usernames, or task content, and that the server stores the request IP and a derived country code with **no automatic expiry**. `CONCORD_TELEMETRY_DISABLED=1` or `DO_NOT_TRACK=1` turns it off, and delivery "can never make a Concord operation fail."

**One direct conflict with this repo's rules.** `src/domain/overlap.ts` decides whether two agents' declared modules/domains/risk-tags mean the same thing by lowercasing, splitting on `[^a-z0-9]+`, and intersecting token sets — so `"Todo Frontend"`, `"todo-frontend"` and `["frontend","todo"]` are treated as the same surface. That is precisely the case-normalise-and-tokenise-to-compare pattern this project bans, applied to the "do these two things mean the same thing" judgement. The strings are agent-declared labels rather than user content, so it is not a clean violation, but vendoring that file as-is would import the exact failure mode (`Work Window` vs `Deep Work Block Duration`) the ban exists to prevent.

**Vendorable:** yes, MIT, TypeScript, cleanly separated domain logic. `presence.ts` and `hook.ts` are each under 250 lines and are the two files worth reading again when we build.

---

## 3. mcp_agent_mail (`Dicklesworthstone/mcp_agent_mail`)

MIT with an OpenAI/Anthropic rider. 2,131 stars, 227 forks, commits daily (latest 2026-09-06). **One contributor.** Roughly 90k lines of Python, of which `app.py` alone is 14,765. The star count says people liked the idea; the contributor count says one person holds all of it.

**What it enforces — almost nothing, by explicit design.** The README is honest about this and repeats it a dozen times. From the tool reference:

> `file_reservation_paths(...)` records an advisory lease in DB and writes JSON reservation artifacts in Git; conflicts are reported if overlapping active exclusives exist (**reservations are still granted; conflicts are returned alongside grants**).

The reasoning, from the FAQ: "Agents coordinate asynchronously; hard locks create head-of-line blocking and brittle failures. Advisory reservations surface intent and conflicts while the optional pre-commit guard enforces locally where it matters." That is a defensible position, but it means the MCP tool never arbitrates.

There are two real enforcement points, both narrow:

1. **Mail-archive writes.** With `FILE_RESERVATIONS_ENFORCEMENT_ENABLED=true` (the default) the server blocks message writes conflicting with an active exclusive reservation — but only for `agents/`, `messages/`, `attachments/`. It protects its own store, not your code.

2. **A generated pre-commit hook** (`src/mcp_agent_mail/guard.py`, installed with `guard install`), which defaults to **block**, not warn: `MODE = os.environ.get("AGENT_MAIL_GUARD_MODE","block")`. It expands staged paths including both sides of renames and refuses the commit on a conflicting reservation, with `AGENT_MAIL_BYPASS=1` as an emergency escape.

Commit-time enforcement is a genuinely good idea that neither of the other candidates has, because **it is harness-agnostic**: it catches Codex, a cloud agent, and a human at the keyboard equally, where a `PreToolUse` hook only catches the harness it is installed in. But note the gate at the top of the generated hook:

```python
GATE_ENABLED = (os.environ.get("WORKTREES_ENABLED","0").strip().lower() in TRUTHY
             or os.environ.get("GIT_IDENTITY_ENABLED","0").strip().lower() in TRUTHY)
if not GATE_ENABLED:
    sys.exit(0)
```

In a shared checkout with no per-agent worktrees — our main case — the guard is **inert unless you explicitly set `GIT_IDENTITY_ENABLED=1`**. It also hard-requires `AGENT_NAME`, exiting 1 without it, which lands us straight back on the colliding-names problem.

**How a claim is released when a session dies — this is the best answer in the field, and it is worth stealing wholesale.** Three independent mechanisms, all in `_expire_stale_file_reservations` (`app.py:4221`) driven by a sweeper on a 60-second interval (`FILE_RESERVATIONS_CLEANUP_INTERVAL_SECONDS`):

- **TTL expiry.** Reservations past `expires_ts` are released. The `UPDATE` runs under `BEGIN IMMEDIATE` "so the release is immediately visible to subsequent reserve calls on other connections (#130)" — they hit and fixed the read-snapshot race.
- **Orphan survival.** The query uses a `LEFT JOIN` on the agent, with the comment: "orphaned reservations whose owning agent has been deleted must still expire on schedule, not pin the path. (#161)". Deleting the holder must not immortalise the lock — a failure they clearly shipped once.
- **Inactivity staleness, probed rather than reported.** `_compute_reservation_activity` (`app.py:1384`) expands the reservation's glob and checks the *actual* latest filesystem mtime and latest git activity against the reserved paths. Default `FILE_RESERVATION_INACTIVITY_SECONDS=1800` with `FILE_RESERVATION_ACTIVITY_GRACE_SECONDS=900`. It runs in `asyncio.to_thread` so a pathological glob "stays off the event loop entirely (#240)".

On top of that, `force_release_file_reservation` lets another agent take over a stale lease: it "validates that the reservation appears abandoned (agent inactive beyond threshold and no recent mail/filesystem/git activity)" and notifies the previous holder with a summary of the heuristics that justified the takeover. That combination — *takeover is allowed, but only against evidence, and the loser is told* — is the single best-designed thing I read today.

Separately, `bd`-style build slots exist with a proper renewal loop: `--ttl-seconds` default 3600, renewed at half the TTL "so leases do not expire on the boundary" (`cli.py:3911`).

**Codex support: real but shallow.** `scripts/integrate_codex_cli.sh` plus per-harness installers for Cursor, Gemini, Cline, Windsurf, Copilot, opencode, and Factory Droid. The MCP tools work anywhere MCP works. The *enforcement* does not travel: the pre-commit guard does (it is git-level), the mail-archive block does, but there is no Codex-side edit interception.

**A cautionary detail about the Claude hook.** The shipped `.claude/settings.json` binds `PreToolUse` on `Edit` to `file_reservations soon --minutes 10` — a listing of reservations about to expire. It prints; it does not block. The repo's own Claude integration is informational, and the `.githooks/pre-commit` actually checked into the repo is a secret-scanner, not the reservation guard. Reading the README you would think the hook enforces. It does not.

**Second tracker: yes, emphatically.** SQLite plus a git-committed archive of `agents/`, `messages/`, `attachments/` and reservation JSON. No GitHub Issues integration. It is a mail system with leases attached, not a tracker, so it would sit *beside* GitHub Issues rather than replace it — but it is still a second store to keep honest.

**Battle-tested?** Genuinely heavy test coverage for a solo project — `test_file_reservation_lifecycle.py` (1,193 lines), `test_concurrency_agents.py` (1,086), `test_guard_worktrees.py` (992) — and the issue numbers in the comments (#130, #161, #240) show real bugs found and fixed in the lease path. But one maintainer and 90k lines is a bus factor of one, and a 140k-character README is a warning sign about how much of the design lives in prose.

**Vendorable:** the *mechanisms* are, and that is what we want. The codebase is far too large to fork for our purposes; `_expire_stale_file_reservations` and `force_release_file_reservation` are ~150 lines of ideas worth reimplementing.

---

## 4. Beads (`gastownhall/beads`, formerly `steveyegge/beads`)

MIT. **26,957 stars, 1,830 forks, 30 contributors**, last commit 2026-09-05. By a wide margin the most battle-tested project in this space, and the one the ticket did not name. Go, with SQLite/Dolt storage and git-backed JSONL sync.

**What it enforces: the only true atomic compare-and-set claim I found.** The `Claimer` interface contract (`issueops/claimer.go`) is unusually precise:

> Claim validates and commits the complete request as one atomic compare-and-set mutation: it sets Assignee to Actor and Status to StatusInProgress **only while** the issue's status is built-in StatusOpen or a configured active status **and** the issue is unassigned, assigned to Actor, or assigned to a configured claim pool. StatusInProgress held by the same Actor is an idempotent success with Changed false and no persisted mutation.
>
> A foreign holder refuses with a wrapped ErrAlreadyClaimed [...] Refusals and deterministic validation failures leave persistent state unchanged. Implementations own transaction retry: a claim that loses a commit-time merge is retried, never surfaced.

The losing caller gets a typed `*ClaimConflictError` carrying the current assignee and status "read inside the attempt that lost", recoverable with `errors.As` without parsing anything. Idempotent re-claim, transaction retry, typed conflict, and a `--claim` flag on the ready-queue pull (`bd ready --claim`) so a fleet of agents can self-select work from one queue and exactly one wins. This is what "claim" should mean, and it is the shape to copy.

One honest limitation stated in the contract: "the actor is caller-asserted provenance, not authenticated identity, and eligibility is decided by the issue's state alone." Any actor may claim as anyone. For a single-user fleet that is fine.

There is also a **merge slot** — "an exclusive-access primitive only one agent can hold at a time", one per project, `bd merge-slot acquire|release|check` — for serialising conflict-prone work like merge-queue conflict resolution. Exactly the primitive our shared checkout wants for anything touching a shared file.

**How a claim is released when a session dies: nothing.** No TTL, no lease, no heartbeat, no sweeper anywhere in the claim path or the merge slot (`cmd/bd/merge_slot.go` has no staleness handling at all — `--holder` is a verification string, not an expiry). The documented release is manual:

```bash
bd assign bd-42 ""              # clear the assignee
bd update bd-42 --status open   # make it claimable again
```

And the coordination doc is blunt about the other half: "**Beads has no agent registry — assignees are plain strings.** To see which agents are active, group in-progress work by assignee." A dead beads agent's claim is permanent until a human clears it. Beads solves arbitration perfectly and liveness not at all — the exact mirror image of Concord.

**Codex support: yes**, as one of eighteen harness integrations (`docs/integrations/` covers codex, claude-code, cursor, gemini, aider, copilot-cli, windsurf, opencode, kiro, junie, factory, cody and more), plus an MCP server. Beads is a CLI, so it is harness-agnostic by construction.

**Second tracker: yes, and this is the disqualifier.** Beads *is* an issue tracker. It has import/export and git sync for its own JSONL format, and **no GitHub Issues bridge** — the integrations directory is entirely agent harnesses, and grepping for a GitHub Issues API call finds only `gh issue list` inside its own `AGENT_INSTRUCTIONS.md`, i.e. beads' maintainers using GitHub for beads' own development. Adopting beads means migrating off GitHub Issues or running both. There is an open `PROPOSAL-cas-conditional-update.md` in the repo, which suggests the compare-and-set surface is still being generalised.

**Vendorable:** MIT and the claim semantics are the valuable part, but it is Go and we are Python. Reimplementing the contract is a day; adopting the tool is a tracker migration.

---

## 5. Agent Board (`jharjadi/agent-board`)

Folders as columns, markdown files as tickets, git as audit log — `todo/`, `doing/`, `review/`, `blocked/`, `done/`, and "a ticket's status is where the file lives." MIT, single `board.py`, self-described experimental.

**Battle-tested: no.** 0 stars, 0 forks, created 2026-09-04 — three days before this note — 30 commits, two contributor logins that appear to be the same person. It is a good blog post attached to a weekend project, and the honest thing to say is that its ideas deserve reading and its code does not deserve adopting.

**What it enforces: nothing that survives a race.** It takes an `fcntl` lock during mutation, but the author states plainly that "two `board take` calls both succeed and the last writer sets owner". Claiming is `board take <id> --owner <name>`, which moves a file between directories.

**Dead holders: nothing, deliberately.** The author explicitly rejected claim expiry and lease management: dead agents leave tickets in `doing/` indefinitely, because "I would rather surface stale work than silently mutate it." That is a coherent philosophy for a human-supervised board of five agents. At 49 sessions with no human watching each one, "a human must notice" is not a mechanism.

**Second tracker: yes** — a directory tree of markdown, duplicating what GitHub Issues already holds natively including the sub-issue and blocked-by edges wayfinder relies on.

The one idea worth keeping: **the audit trail is the git history, and a review argument is a diff rather than a chat bubble.** We get that free from GitHub Issues comments.

---

## 6. The rest of the field

`andyrewlee/awesome-agent-orchestrators` (1.8k stars, updated 2026-09-05) lists ~250 projects. The overwhelming majority are **per-agent-worktree runners, TUI multiplexers, or desktop cockpits** — amux, claude-squad, dmux, Fletch, Garcon, intentic, Ouijit, parallel-code, herdr, Alethe, Berd, Zaivern Code, Aperant and dozens more. They solve "give each agent its own sandbox and show me the panes". We have the opposite problem: one checkout deliberately shared, and no need for a launcher. Dismissed as a class.

A second class — cyrus, aeon, open-swe, no_human, Taskuary — watches an issue tracker and drives an agent per issue. They coordinate *by* assignment upstream, so they inherit exactly the assignee-is-not-a-lock problem in §1 rather than solving it.

Four entries were worth opening:

**swarm-protocol** ([phuryn/swarm-protocol](https://github.com/phuryn/swarm-protocol), MIT, 53 stars, 2 contributors, **dead since 2026-03-15**) is the closest conceptual match to what we would build: MCP tools to claim work, detect file conflicts, heartbeat, and hand off across sessions. It genuinely arbitrates the claim — `claimWork` throws `Intent ${id} is already claimed by ${claimedBy}` (`src/db/queries.ts:268`) — while file conflicts are warnings only, the same split as Concord. Its liveness model is a **self-reported heartbeat**: "Call every 10-15 minutes. Claims with no heartbeat for 30 min get flagged as stale" (`src/tools/claims.ts:29`), implemented as `c.last_heartbeat < now() - interval '30 minutes'`. Two things disqualify it and both are instructive. It requires a **PostgreSQL server** (`docker-compose.yml`) — real infrastructure for what should be a hook. And stale claims are only *flagged* on a dashboard; nothing releases them, so it lands in the same place as Concord. The self-reported heartbeat is also strictly weaker than agent_mail's probe: an agent that is wedged stops heartbeating and looks dead, while an agent looping uselessly keeps heartbeating and looks alive. Evidence beats self-report. Worth noting that its 30-minute staleness threshold independently matches agent_mail's `FILE_RESERVATION_INACTIVITY_SECONDS=1800` — two projects converging on the same number is the best default we have.

**foremerge** ([naw103/foremerge](https://github.com/naw103/foremerge), Apache-2.0, 166 stars, **1 contributor**) is a coordination protocol above git — agents declare intent, semantic scope and operation before writing, "deterministic rules raise findings when plans collide", with MCP for Claude Code, Codex and Cursor over local SQLite in one Rust binary. The right architecture (no server, cross-harness, pre-declaration) but one contributor and, per its own description, findings rather than refusals. Its plan-collision-before-writing idea is the most novel thing in the list; it is also the thing our `PreToolUse` block achieves more cheaply.

**wit** ([amaar-mc/wit](https://github.com/amaar-mc/wit), MIT, 46 stars, 1 contributor, **dead since 2026-03**) locks individual *functions* rather than files, using Tree-sitter to warn agents of conflicts before they write. Genuinely clever — file-level locks are too coarse when two agents edit different functions in one module, which is our common case. But it warns rather than blocks, it is abandoned, and function-granularity locking is a large amount of machinery to maintain for a second-order improvement. Park the idea; it is the right *next* refinement if file-level claims prove too coarse.

**paperclip** ([paperclipai/paperclip](https://github.com/paperclipai/paperclip), MIT, **80.1k stars**, 30 contributors, active) is the largest project in the list by an order of magnitude: a self-hosted platform where "agents wake on heartbeats to claim tickets, governed by org charts, budgets, and approval gates". It is a company-scale agent-management product with its own ticket system, org model and approval workflow. The star count demands it be mentioned; the shape rules it out. We do not need org charts and budgets, and adopting it means moving the work off GitHub Issues into its platform — the §9 disqualifier again, at maximum weight.

Also noted and dismissed on shape: **gastown** ([gastownhall/gastown](https://github.com/gastownhall/gastown), MIT, 17.9k stars, 30 contributors) — 20-30 agents with a coordinator, health watchdogs and a Bors-style merge queue, backed by beads for issue tracking. It is the orchestrator to beads' tracker from the same org, and it wants to own agent lifecycle, which ours does not surrender. **NEEDLE** (23 stars) runs headless agents against a beads queue with atomic claims and states outright "no inter-agent channel, coordination is done at decomposition time" — a legitimate strategy, and roughly what wayfinder's blocked-by edges already do for us. **corellis** (29 stars, **0 contributors listed**, last push 2026-04) and **loki-mode** (BUSL-1.1, source-available, not open source) fail on maintenance and licence respectively. **NXTG-Forge Orchestrator** (159 stars, 2 contributors, no declared licence) claims file locking across Claude Code, Codex and Gemini on one shared repo — the right target, but an unlicensed two-person project is not adoptable.

---

## 7. Hook mechanics: what can actually enforce anything

Everything above rests on what a hook can and cannot do, so this is from the official reference ([Claude Code hooks](https://code.claude.com/docs/en/hooks)).

**`PreToolUse` really can block, two ways.** Exit code 2 is the blunt instrument: "Exit 2 means a blocking error. On events that can block, exit 2 blocks whether or not you print JSON: even a JSON `permissionDecision` of `"allow"` can't override it." The structured form is `hookSpecificOutput.permissionDecision: "deny"` with a `permissionDecisionReason`. Either way the tool call does not happen. **This is the only true enforcement seam available to us inside Claude Code**, and it is the one Concord uses.

**Every hook payload carries `session_id`.** It is listed among the common input fields — "`session_id` | Current session identifier" — on all events. So the stable, collision-free identity we need is handed to the hook for free, and never has to be inferred from a display name.

**`SessionEnd` cannot be trusted to release anything, and its own documentation shows why.** The enumerated `reason` values are `"clear"`, `"resume"`, `"logout"`, `"prompt_input_exit"`, and `"other"`. Every one of those is an *orderly* termination — the user cleared the conversation, resumed elsewhere, logged out, or typed exit. **There is no reason value for a crash, a SIGKILL, a closed terminal, or an exhausted context**, which are precisely the deaths that strand a claim. The documentation is otherwise silent on process termination: the event table says only "`SessionEnd` | When a session terminates", and nothing states whether the hook runs when the process is killed. Silence in a reference is not a guarantee, and a lock whose release depends on an undocumented behaviour of a signal handler is a lock that will leak. This is the direct evidence behind the non-requirement in §9: expiry must work with the holder's machine unplugged, and `SessionEnd` is at best a fast-path optimisation.

**There is no liveness primitive in the harness at all.** No heartbeat, no TTL, no keepalive anywhere in the hook reference. The only `timeout` is per-hook execution ("Seconds before canceling", defaulting to 600 for command hooks). Anything resembling a lease must be built on top — which is why all three serious candidates built their own presence layer, and why the transcript mtime finding in §1 matters as much as it does.

The event list is long (33 events, including `SubagentStart`/`SubagentStop`, `PreCompact`/`PostCompact`, `TaskCreated`/`TaskCompleted`, and a `TeammateIdle`), so there is room to make releases prompt on the happy path. `Stop` — which Concord uses in place of `SessionEnd` — fires at the end of every turn and is the most frequent honest signal that a session is still alive.

**Codex has hooks too, and they are the same three Concord uses.** `~/.codex/config.toml` accepts `[[hooks.SessionStart]]`, `[[hooks.PostToolUse]]` and `[[hooks.Stop]]` tables with `type = "command"` entries (evidenced by `src/install/codex-config.ts`, which writes exactly those and treats them as the supported set). Two differences matter: Codex passes its session id **on stdin rather than in the environment**, so a cross-harness hook must read the payload rather than an env var; and Concord's own capability matrix records Codex hooks as `busy`-only — "an idle Codex session runs no hook" (`src/domain/delivery.ts:39`). Critically, **the Codex hook set does not include `PreToolUse`**, so the edit-blocking seam that exists in Claude Code has no Codex equivalent. Cross-harness enforcement therefore has to happen at a layer both share, which is the git pre-commit hook — exactly agent_mail's bet.

---

## 8. Comparison

| | Concord | mcp_agent_mail | Beads | Agent Board |
|---|---|---|---|---|
| Atomic claim (a loser exists) | task ownership only | no — grants and reports conflict | **yes, typed CAS** | no, last writer wins |
| Edit-collision block | **yes, `PreToolUse` exit 2** | commit-time only, gated off by default | no | no |
| Dead-holder release | roster decays, **claim does not** | **TTL + orphan + activity sweeper** | nothing | nothing, by choice |
| Evidence-based takeover | no | **yes, with notification** | no | no |
| Codex enforcement | hooks in `config.toml` | pre-commit (harness-agnostic) | CLI, so yes | n/a |
| Sits on GitHub Issues | no | no | no | no |
| Stars / contributors | 315 / 4 | 2.1k / **1** | **27k / 30** | 0 / 1 |
| Licence | MIT | MIT+rider | MIT | MIT |

Read down the "sits on GitHub Issues" row: **every candidate introduces a second source of truth.** That is not four independent failures to integrate; it is a structural fact about the field. These tools were built for fleets that had no tracker, so they each shipped one. We have a tracker, and it already holds the map/ticket/blocked-by structure wayfinder depends on.

---

## 9. Recommendation: AUTHOR

Not because the prior art is bad — Beads' claim contract and agent_mail's sweeper are better than anything we would invent unaided — but because of three facts that no amount of configuration changes:

1. **Every candidate replaces GitHub Issues.** Adopting any of them means either migrating the wayfinder maps off native sub-issues and blocked-by edges, or running two trackers and reconciling them by hand across 49 sessions. The ticket's own bar was that a second source of truth must earn its place; none of these earns it, because none of them offers anything that could not be layered *onto* issues instead.

2. **The one project that solves dead holders solves nothing else, and vice versa.** Beads has atomic claims and no liveness. Concord has liveness and does not connect it to the lock. agent_mail has the best expiry design and explicitly refuses to arbitrate. Adopting any one leaves the specific failure we have already suffered — a claim held by a session that no longer exists — unfixed.

3. **We already have the liveness signal none of them had.** All three build presence infrastructure — registries, heartbeats, `last_seen` columns — because a generic tool cannot see inside the harness. We can: the transcript JSONL mtime is per-session, unique-by-UUID, updated every turn, and free. 7 of 32 transcripts were live in the last 15 minutes when measured. The expensive part of a lease is already sitting on disk.

The thing to author is small: a skill plus one hook, with GitHub Issues as the durable record.

### Requirements for the skill author

**Identity**
- Key every claim on the **session UUID**, never the display name. Names collide three-ways today; the UUID is unique and is already the transcript filename.
- The claim record on the issue must carry: session UUID, worktree path, branch, and the wall-clock time of the claim. A comment is the right vehicle — it is durable, visible to Codex via `gh`, and already the convention the #319 recovery used.
- Follow Beads: the actor is caller-asserted, not authenticated. Do not build an identity system for a single-user fleet.

**Claiming**
- The GitHub assignee stays as the *visible* claim, because wayfinder's frontier query already reads it and a human can see it in the UI. But it is a record, not a lock — the REST endpoint is set-union with no conditional write, so never write code that assumes an assignee add can fail.
- Arbitration needs a primitive that *can* refuse. Take Beads' contract verbatim: set holder and status in one compare-and-set, succeed only from unclaimed-or-mine, make re-claim by the same holder idempotent with no write, and return a **typed conflict carrying the current holder read inside the losing attempt** — not a parsed error string.
- Provide a claim-next-from-frontier operation, not just claim-this-one. `bd ready --claim` is the shape: N sessions pull from one queue and exactly one wins each ticket.

**Lease expiry and stale takeover** — this is the part we are actually missing, and the design is agent_mail's:
- A claim is a **lease with a TTL**, not a flag. Renew at half the TTL so leases never expire on the boundary (`cli.py:3911`).
- Expiry must be **three-legged**, because each leg catches a different death: elapsed TTL; holder-record-gone (use an outer join — an orphaned claim must still expire, not pin the ticket forever, which is agent_mail's #161); and **inactivity probed against evidence**, not self-reported. Our probe is the transcript mtime plus git activity on the claimed paths. Default to agent_mail's numbers until we measure our own: 30-minute inactivity, 15-minute grace.
- **Takeover is allowed but must be justified and announced.** Model `force_release_file_reservation`: validate the lease looks abandoned against the heuristics, then post the takeover and the evidence as a ticket comment naming both sessions. A silent steal is worse than a stuck claim; an announced, evidenced one is better than both.
- Make the release write immediately visible to concurrent readers. agent_mail needed `BEGIN IMMEDIATE` to stop a stale read snapshot re-granting a released lease (#130); on GitHub, re-read the issue after the write rather than trusting the response body.

**Collision detection**
- Enforce at `PreToolUse` on `Edit|Write|MultiEdit`, blocking with exit 2, copying Concord's `decidePreToolUse` shape.
- **Copy Concord's degradation rule exactly:** when the hook cannot identify its own claim, warn, never block. Blocking an agent from editing its own files is worse than the collision it prevents.
- Also enforce at **pre-commit**, which is the only layer that catches Codex, cloud agents, and a human at the keyboard alike. Ship it enabled — agent_mail's guard defaults to `block` but is gated behind `WORKTREES_ENABLED`/`GIT_IDENTITY_ENABLED`, so in a plain shared checkout it silently does nothing. Do not repeat that; a guard that is inert by default is a guard nobody knows is off.
- Keep an emergency bypass (`AGENT_MAIL_BYPASS=1` is the precedent) and make its use loud.
- **Do not port Concord's `overlap.ts`.** It decides whether two declared scopes mean the same thing by lowercasing and splitting on `[^a-z0-9]+`. Compare paths, which this system minted; if we ever want to compare *descriptions* of scope, that is a judgement and it goes to a model.

**Non-requirements**
- No daemon, no server, no hosted dependency. Everything above is a hook, a `gh` call, and a stat of a file that already exists.
- No agent registry. Beads has none and does not need one; the live set is derived by listing claims and probing their holders.
- No `SessionEnd`-based release as the primary mechanism. Concord, purpose-built for this, binds `SessionStart`/`PostToolUse`/`Stop` and not `SessionEnd`. A hook that runs on graceful exit does not fire for the deaths we care about — SIGKILL, a closed terminal, an exhausted context. Treat any end-of-session release as an optimisation that makes recovery faster, never as the thing correctness rests on. Expiry must work with the holder's machine unplugged.

### Cost

Low. The claim/lease/takeover logic is a few hundred lines over `gh` plus a stat; the `PreToolUse` hook is Concord's 80-line decision function; the pre-commit guard is agent_mail's, minus the gate. The expensive design work — what expiry legs are needed, how takeover should be justified, why presence must decay, why the loser needs a typed conflict — has been done for us by three projects that each learned one third of it the hard way.

---

## Claims that could not be sourced to a primary document

- **The mcpmarket listing for Concord** (E2EE, signal decay, quorum voting) could not be fetched — the site returned HTTP 429 on two attempts. Those features appear nowhere in `Get-Concord-AI/concord-mcp`; the listing appears to blend two unrelated projects named Concord. The Show HN thread (`item?id=49464704`) also returned 429 and was not read.
- **Agent Board's source** was characterised from the author's dev.to post and repository metadata, not from reading `board.py`. The quoted lines about `fcntl` and last-writer-wins are the author's own description of his implementation.
- **Beads' storage-layer claim implementation** was read at the interface-contract level (`issueops/claimer.go`) and through `ParseClaimConflict`; the SQL performing the compare-and-set inside the Dolt/SQLite backends was not read line by line.
- **In §6, only swarm-protocol's source was read.** foremerge, wit, paperclip, gastown, NEEDLE, corellis, loki-mode and NXTG-Forge were assessed from repository metadata (stars, forks, contributor count, last push, licence, all via the GitHub API on 2026-09-07) plus the awesome-list's own one-line descriptions. Their feature claims are therefore *reported*, not verified — the enforce-versus-document distinction has not been checked in their code. Each was dismissed on maintenance, licence, server dependency, or architectural shape rather than on a claim about its internals, so verifying them would not change the recommendation; but if any is revisited, read the source first.
- The **49 live sessions** figure is from the ticket. What I measured directly on 2026-09-07 was 32 transcripts across seven project directories for this checkout, 7 of them written within 15 minutes — the ticket's count presumably includes sessions on other projects or under other registries.

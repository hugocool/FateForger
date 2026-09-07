# Release as infrastructure: the Stop hook and the orphan sweeper

Resolves [#371](https://github.com/hugocool/FateForger/issues/371) on map
[#364](https://github.com/hugocool/FateForger/issues/364). Builds on the claim protocol decided
in [#369](https://github.com/hugocool/FateForger/issues/369) and the prior art in
[#368](https://github.com/hugocool/FateForger/issues/368). Written 2026-09-07.

Code: `scripts/coordination/`. Tests: `tests/coordination/test_claim_liveness.py`.

---

## The shape of the answer

The hook is the small half. The sweeper is the load-bearing half, and the ticket said so before
any of this was built: *the failure modes that strand a claim are the ones where the agent can
no longer act.* What the work here adds is a reason the hook is smaller still than the ticket
assumed, and a liveness predicate that survives contact with this machine.

| | covers | costs |
|---|---|---|
| `Stop` hook | a session that is still alive to be helpful | zero network calls in the common case |
| sweeper | SIGKILL, closed laptop, abandoned tab, context exhaustion, Codex | 1 REST call + 1 per claim, on demand, never polled |

---

## Why the hook does less than the ticket assumed

`SessionEnd` was already ruled out by #368: its documented reasons are all orderly exits, it
never fires on context exhaustion (that path auto-compacts), and it is unreliable on the happy
path (anthropics/claude-code#79702). That left `Stop`.

**Verified against the official hook reference on 2026-09-07:** `Stop` fires *every time the
main agent finishes responding* — once per **turn**, not once per session. It takes no matcher.
Its default timeout is 600 seconds.

That single fact reshapes the hook. A `Stop` hook that unconditionally dropped this session's
claims would give every claim a lifetime of one turn, and a session driving a ticket across
twenty turns would lose its claim after the first — while still believing it held it. Two
sessions both believing they hold an issue is a strictly worse failure than a stale ref,
because a stale ref is *cheap to detect* (a dead pid is proof) and a double grant is not
detectable at all. The asymmetry is the whole reason the ref is the arbiter.

So `claim_stop_hook.py` does two things, and neither of them guesses:

1. **It stamps a turn boundary** into a local ledger. Free, no network, no GitHub.
2. **It releases claims that opted in** — those created with `--release-on-idle`, the mode for
   one-shot and automated sessions whose turn end really is the end of their work.

### The stamp is the thing only a Stop hook knows

`(pid, procStart)` proves a process is *running*. It cannot tell a session working from a
session abandoned in a browser tab three days ago — and "abandoned terminal" is one of the three
failure modes the ticket names. A turn boundary is the only free observation that separates
them, and `Stop` is the only place it is observable.

This is the transcript-JSONL-mtime signal from #368, one step better: transcripts are keyed by
session id, and **session id does not identify a process on this machine** (see below).

The sweeper reads the stamp and reports a long-idle live holder as `idle-suspect`. It never
takes one. Idle age is a suspicion; only a dead pid is proof.

### The failure path, which the ticket asked for explicitly

Network down, `gh` unauthenticated, GitHub 500: all caught. The hook writes
`release_failed_at` and the reason into the ledger, tells the agent through
`hookSpecificOutput.additionalContext`, and **exits 0**. It never exits 2 and never returns a
blocking decision — a `Stop` hook that blocks traps the session in a loop, and this one has
nothing worth stopping a turn for. The unreleased ref then falls to the sweeper, which is
exactly the division of labour the design wants.

Exercised, not assumed: `gh` was shadowed by an always-failing stub on `PATH` and the hook
returned 0, recorded `moved: HTTP 0 gh: connection refused (HTTP 000)`, and left the ref
standing for the sweeper.

---

## Two findings that change the protocol

### 1. `CLAUDE_CODE_SESSION_ID` does not identify a process

`#369` specified reading `(pid, procStart)` from `~/.claude/sessions/<pid>.json`. The registry is
keyed by pid; a script knows its **session id**. The join between them is not one-to-one.

**Measured on this checkout, 2026-09-07: 48 live claude processes carry only 25 distinct
session ids.** Thirteen session ids had 2–4 live registry entries apiece, because resuming a
conversation starts a second process under the same id and leaves the first running. This
session's own id resolved to two live pids — 78039 (`admonish-1-2c`) and 92476
(`admonish-1-18`, whose command line carries `--resume=<this id>`).

A claim that records the wrong one of those is released the moment the *other* window is closed.
Scanning the registry for `sessionId == CLAUDE_CODE_SESSION_ID` and taking a match is a coin
flip between live processes.

The reliable join runs the other way: **walk up our own process ancestry to the nearest pid that
has a registry entry.** That pid is, by construction, the process actually running us. The
session id becomes a consistency check rather than the lookup key. `claim_lib.identify()` does
this; `claim.py whoami` prints the result.

This also sharpens the map's existing "the addressable names collide" note. It is not only the
`admonish-1-XX` display names that are ambiguous. The session id is ambiguous too, one level
down.

### 2. The timezone trap, measured and then tested

`procStart` in the session registry and `ps -o lstart=` use the **same string layout** and
**different timezones**. The registry writes UTC; `ps` prints local.

Measured across all 48 registry entries on this machine:

| comparison | agreed |
|---|---|
| naive string equality | **0 of 48** |
| registry parsed as UTC vs `ps` parsed as local | **48 of 48, to 0.000 s** |

That first row is the confident, completely wrong "48 stale" reading the research warned about,
reproduced. `test_claim_liveness.py` pins the second row in five timezones, and the tests were
verified non-vacuous by sabotage: reverting `procstart_epoch_utc` to a local parse fails two
tests; making the sweeper take idle-suspects and expired-remotes fails two more.

Belt and braces: at claim time the payload stores `proc_start_epoch` computed from **this
machine's own `ps`**, not from the registry string. That is self-calibrating — nothing
downstream has to assume which timezone the registry was written in — and the UTC parse is the
fallback for a payload that lacks it.

### The predicate that falls out

```
dead      pid absent from ps                        -> proof
recycled  pid present, start time > 2s away         -> proof (what `kill -0` gets wrong)
alive     pid present, start time matches            -> leave alone
unknown   no comparable start time recorded          -> conclude nothing
```

`unknown` is deliberate. Absence of evidence is never read as evidence of death.

---

## The sweeper

`claim_sweep.py` walks `refs/claims/*`, reads each payload, and classifies. **Report-only by
default;** `--apply` deletes, and deletes nothing but proven orphans.

| verdict | means | what the sweeper does |
|---|---|---|
| `orphaned` | same machine, pid dead or recycled | rung 1 — deletes it under `--apply` |
| `idle-suspect` | same machine, alive, no turn finished in `--idle-hours` | reports; names the session to message |
| `expired-remote` | off-machine, past TTL | reports; rung 2/3/4, including "if it is Codex, escalate" |
| `held` / `held-remote` | holder is live, or within TTL | nothing |
| `unreadable` | payload absent or unparseable | nothing — it may be a newer schema |

**Rungs 2–4 need somebody to ask the holder and then wait.** A shell script cannot do that and
must not fake it, so the sweeper prints them as a work list with the address to message. The
`next_step` text is the rulebook, delivered where it is needed. Reporting a takeover you did not
verify is worse than not sweeping.

### Safety under 49 concurrent sessions

- **Compare-and-delete.** Before deleting, the ref is re-read and its object sha compared to the
  one that was listed. This narrows — it does **not** close — the window where we read a dead
  holder's claim, the holder released, a fresh session claimed, and we then delete the fresh
  claim. GitHub's ref `DELETE` has no `If-Match`. Saying "narrows, not closes" out loud is
  better than implying atomicity we do not have. Verified: deleting with a stale expected sha
  returns `moved` and leaves the ref standing.
- **Double delete is harmless.** Two sweepers reaping one orphan get `deleted` then `absent`;
  neither errors. Verified.
- **Unreadable is not moved.** If the pre-read fails because `gh` is broken, the outcome is
  `error`, not `moved` — we know nothing, and must not claim a fresh holder exists.
- **Budget floor.** All 49 sessions authenticate as one user and share one bucket. The sweeper
  reads `/rate_limit` (which does not count) and refuses to run below 500 remaining. It is
  1 call + 1 per claim, on demand. **Never poll it.**

---

## Operational constants, named so they can be tuned

#369 deliberately left two numbers open. They are named in one place each rather than
scattered:

| constant | value | where |
|---|---|---|
| `DEFAULT_TTL_HOURS` | 24 | `claim_lib.py` |
| `DEFAULT_IDLE_HOURS` | 6 | `claim_sweep.py` |
| `PROC_START_TOLERANCE_S` | 2.0 | `claim_lib.py` |
| `REST_BUDGET_FLOOR` | 500 | `claim_lib.py` |

The TTL is long on purpose. On-machine liveness is free and exact, so the TTL only has to cover
holders that cannot be probed, and a short TTL buys renewal writes against a budget of ~10
content writes per agent per hour shared by 49 sessions. Both numbers want a measurement rather
than an opinion; #372 owns picking better ones.

---

## What was verified, and how

Everything below ran against the real repo, in the `refs/claimprobe/*` namespace, and was
cleaned up afterwards — verified clean with a raw `gh api` call, not through this code.

| claim | evidence |
|---|---|
| a ref can point at a blob; second create returns 422 `Reference already exists` | ran it; `won` then `taken (HTTP 422)`, payload read back intact |
| **a SIGKILLed holder's claim is detected and swept** | a child process SIGKILLed itself (`returncode == -9`); an `atexit` marker was *absent*, proving no orderly path ran; the sweeper reported `held` before and `orphaned: no live process with pid 8159` after; `--apply` deleted the ref; the ref was gone on re-read |
| pid reuse does not look like liveness | live pid, start time 1 h off → `orphaned: … the pid was reused` |
| an off-machine expired claim is reported, never auto-taken | `--apply` left the ref standing; verified it survived |
| the Stop hook releases an opted-in claim | ref gone, ledger entry gone, `rc=0` |
| the hook survives a broken `gh` | stub on `PATH`; `rc=0`, failure recorded in the ledger and surfaced to the agent, ref left for the sweeper |
| two sweepers racing one orphan | `deleted` then `absent`, no error |
| compare-and-delete refuses a moved ref | stale expected sha → `moved`, ref untouched |
| the timezone comparison | 48/48 agreement, 0/48 by string; 25 unit tests; sabotage confirmed 4 of them fail when broken |

### What was **not** verified, in those words

- **A real Claude Code session was never killed.** Forty-nine live sessions share this checkout
  and they are other people's work. The end-to-end test killed a process this test spawned, not
  a session. The sweeper's predicate does not distinguish the two — a SIGKILLed process is
  absent from `ps` either way — but the claim "a killed *session* was observed to release its
  claim" is one I did not earn, and I am not making it.
- **The hook was never run by Claude Code.** It was run by hand with a synthetic `Stop` payload
  matching the documented schema. Installing it into a live `settings.json` would have applied it
  to all 49 sessions at once, which the ticket forbade. The settings fragment is proposed, not
  applied.
- **Nothing off this machine was tested.** `expired-remote` was exercised with a fabricated
  `machine_id`, not a real second machine or cloud agent.
- **No Codex session was tested.** Codex falls to the TTL alone by construction — it has neither
  a registry entry nor an address — and that path is `expired-remote`, which was exercised with
  a fabricated holder. Codex's own leaked lockfiles in `~/.codex/thread-writer-locks/` remain
  the live example of the failure being prevented.
- **The idle-suspect path was unit-tested, never observed.** It needs a session that finishes a
  turn and then goes quiet for six hours with the hook installed.

---

## The failure this design still has

If a session holds a claim and its process stays alive but nobody is driving it, the sweeper
will report `idle-suspect` and stop. Somebody has to message it. That is deliberate — the
alternative is a script deciding, on a timer, that a live process is not really working, which
is exactly the kind of silent wrong answer the rest of this repo is built to avoid — but it does
mean the abandoned-tab case still ends at a human or at another agent's judgement, not at
automation.

The progressive-disclosure principle from #369 says that is the right place for it to end. The
ref answered "taken, and roughly what for" for one cheap call. The message answers "are you
still on this". Hugo answers only what neither could.

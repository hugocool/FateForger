# Peer-session claims

Who is on which issue, across ~49 Claude Code sessions and Codex sharing one checkout.

A claim is the git ref `refs/claims/<issue>`. Creating it twice returns 422
`Reference already exists`, so exactly one caller wins **and the loser is told** — the property
`gh issue edit --add-assignee` lacks. Exclusivity forces the exact ref name, so the holder's
identity lives in the object the ref points at: claiming is create-blob-then-create-ref.

Decided in [#369](https://github.com/hugocool/FateForger/issues/369); release and sweep built in
[#371](https://github.com/hugocool/FateForger/issues/371). Design note:
`docs/superpowers/research/2026-09-07-claim-release-and-sweep.md`.

## Use it

Stdlib only, needs no venv and no `PYTHONPATH`. Authenticates through `gh`.

```sh
python3 scripts/coordination/claim.py whoami
python3 scripts/coordination/claim.py acquire 371 --intent "one line: what you are doing"
python3 scripts/coordination/claim.py list
python3 scripts/coordination/claim.py release 371

python3 scripts/coordination/claim_sweep.py            # report only
python3 scripts/coordination/claim_sweep.py --apply    # delete claims PROVEN dead
```

`acquire` exits 3 when somebody else already holds it. Echo a claim and a release to humans with
one comment on the issue each — **never poll GitHub**; all sessions share one rate-limit bucket
and the board is read from refs, which are cheap.

Test against `--namespace claimprobe` rather than `claims`, and clean up after yourself.

## The sweeper acts only on proof

A dead pid is proof, so the sweeper takes those. Everything else it *reports*, with the address
to message:

| verdict | what to do |
|---|---|
| `orphaned` | proof. `--apply` deletes it; record the takeover on the issue |
| `idle-suspect` | alive but no turn finished lately. Message the session. Never take it |
| `expired-remote` | off-machine, past TTL. Message it; if it is Codex, escalate to Hugo |
| `held` / `held-remote` | leave alone |
| `unreadable` | leave alone — it may be a newer schema |

## The hook

`claim_stop_hook.py` binds `Stop`, not `SessionEnd`. **`Stop` fires at the end of every turn**,
so it does not release by default: it stamps a turn boundary into a local ledger (free, no
network) and releases only claims made with `--release-on-idle`. Everything else is the
sweeper's job.

`settings.hooks.json` is the proposed settings fragment. **It is not installed.** Merging it is
a human's call, because `~/.claude/settings.json` and `.claude/settings.local.json` are live for
every session on this machine at once.

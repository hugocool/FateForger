"""A DeepSeek Harness ``PreToolUse`` hook that will not let a plan reach the
calendar unless a human said so.

The harness path has no review stage — unlike the legacy flow, which lost its
gate in one commit, ``/dsh`` was born without one. Every plan it commits is
recorded in the journal as ``ACCEPTED``, and that disposition is a training
label feeding the constraint memory server. So an unattended commit does not
merely change a calendar: it teaches the system that Hugo approved a day nobody
ever showed him, and the corpus does not forget.

**Approval is a button press, never a reading of prose.** Deciding that "yeah
go on then" means yes is a judgement about meaning, and this project routes
those to a model rather than a pattern — but putting a model inside the one
path that protects the calendar adds a failure surface exactly where failure is
least acceptable. A Slack button is an identifier this system minted. Comparing
it is identification, not interpretation, and there is nothing in it to get
subtly wrong.

**Deny is the default and absence never approves.** A missing token, an
unreadable file, a malformed one, a token for a different plan — every one of
them denies. The failure mode of a wrongly-denied commit is that Hugo presses a
button; the failure mode of a wrongly-allowed one is a calendar he did not
agree to, discovered later, recorded as consent.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Set per-run by ``harness_bridge`` to the file a Slack approval writes into.
#: Absent means no one is watching this run -- a headless invocation rather
#: than a Slack turn -- and the gate holds shut rather than assuming consent.
APPROVAL_FILE_ENV = "FF_DSH_APPROVAL_FILE"

#: Only tools that write to the real calendar are gated. Reading, drafting and
#: patching a local plan are all reversible and cost nothing to get wrong.
GATED_TOOLS = frozenset({"mcp__tmbx__plan_commit"})

_DENY_REASON = (
    "This plan has not been approved. Show Hugo the day and ask him to press "
    "Approve in Slack; the button is the only thing that opens this. Do not "
    "retry plan_commit until he has."
)


def gate_decision(event: dict, approval_path: str | None) -> dict | None:
    """Return a hook decision, or ``None`` to leave the call alone.

    ``tool_name`` is harness-minted, so matching it is identification rather
    than a judgement about anything a person wrote.
    """
    tool = event.get("tool_name")
    if not isinstance(tool, str) or tool not in GATED_TOOLS:
        return None

    if not approval_path:
        return _deny("no approval channel for this run")

    try:
        raw = Path(approval_path).read_text(encoding="utf-8").strip()
    except OSError:
        # Unreadable is not approved. A gate that opens when it cannot check
        # is not a gate.
        return _deny("approval could not be read")

    if not raw:
        return _deny("not approved yet")
    return None


def _deny(detail: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{_DENY_REASON} ({detail})",
        },
        "decision": "deny",
        "reason": f"{_DENY_REASON} ({detail})",
    }


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        # A malformed event names no tool, so nothing here can establish that
        # a commit was approved. Deny rather than wave it through: this is the
        # one hook where the unparseable case must not become the open case.
        print(json.dumps(_deny("unreadable hook event")))
        return 0

    if not isinstance(event, dict):
        print(json.dumps(_deny("unreadable hook event")))
        return 0

    decision = gate_decision(event, os.environ.get(APPROVAL_FILE_ENV))
    if decision is not None:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

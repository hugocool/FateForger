"""The per-session record for #391: what Stage 1 asked, what was answered, what was set aside.

Read-only over `data/admonish.db`. Prints the arithmetic half of the record map #382's
sessions ticket asks for, so the human writes only the half that needs a human:
*missed*, *scanned*, *wrong*, *sprint-shaped*. Nothing here judges anything; every
line is a field the session already wrote.

    PYTHONPATH=src ./.venv/bin/python scripts/session_record.py                 # newest 3 sessions
    ...                                                        --day 2026-09-08
    ...                                                        --key C0AA6HC1RJL:1788854989.942209
    ...                                                        --latest 5

"Scanned" is the one field the snapshot cannot show: a panel open leaves no fact.
Count it from the bot log instead — every open logs `rules panel opened session_key=…`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter

DB = os.environ.get("ADMONISH_DB", "data/admonish.db")


def _rows(where: str, params: tuple) -> list[tuple]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return conn.execute(
            "select session_key, revision, status, planning_date, updated_at, snapshot_json "
            f"from timeboxing_session_states {where} order by updated_at desc",
            params,
        ).fetchall()
    finally:
        conn.close()


def _cell_label(cell_id: str) -> str:
    # `elicit.<row>.<criterion>`: identifiers the system minted, split on its own separator.
    parts = cell_id.split(".")
    return f"{parts[1]} / {parts[2]}" if len(parts) == 3 else cell_id


def record(key: str, revision: int, status: str, planning_date: str | None, updated: str, raw: str) -> None:
    snap = json.loads(raw)["snapshot"]
    day = snap.get("planning_day") or {}
    facts = snap.get("facts", [])
    rules = snap.get("applicable_constraints", [])
    by_uid = {r["uid"]: r for r in rules}

    print(f"\n\x1b[1m{planning_date or '(no day)'}  {day.get('day_type', '?')}\x1b[0m  {key}  rev {revision}  {status}  stage1={snap.get('stage1')}  saved {updated[:16]}")

    asked_for = [f["value"] for f in facts if f["kind"] == "requested_activity"]
    print(f"  asked for: {asked_for or '—'}")

    # --- the matrix: what Stage 1 believed it still needed -------------------
    matrix = next((f["value"] for f in facts if f["kind"] == "coverage_matrix"), None)
    if matrix is None:
        print("  matrix: none — Stage 1 never classified (loop not live for this session)")
    else:
        cells = matrix.get("cells", {})
        states = Counter(cells.values())
        unaskable = set(matrix.get("unaskable", []))
        print(f"  matrix: {dict(states)}  unaskable={len(unaskable)}")
        open_cells = [c for c, s in cells.items() if s == "uncovered"]
        if open_cells:
            print("  still open: " + ", ".join(_cell_label(c) + (" (unaskable)" if c in unaskable else "") for c in open_cells))

    # --- asked and answered ----------------------------------------------------
    answers = [f for f in facts if f["kind"] == "elicited_statement"]
    print(f"  \x1b[1masked → answered ({len(answers)})\x1b[0m")
    for f in answers:
        v = f["value"] if isinstance(f["value"], dict) else {"cell": None, "text": f["value"]}
        cell = v.get("cell")
        # A statement recorded before the loop was live carries no cell.
        label = _cell_label(str(cell)) if cell else "(no cell: pre-loop)"
        print(f"    {label:<34} {str(v.get('text', ''))[:100]}")
    pending = snap.get("pending_blocker")
    if pending:
        opts = ", ".join(o.get("label", "") for o in pending.get("options", []))
        print(f"    \x1b[2mwaiting on: {_cell_label(pending['requirement_id'])}" + (f"  [{opts}]" if opts else "") + "\x1b[0m")

    # --- forced past a cell ----------------------------------------------------
    forced = [a for a in snap.get("assumptions", []) if a.get("filed_by") == "user"]
    if forced:
        print(f"  \x1b[1mforced past ({len(forced)})\x1b[0m")
        for a in forced:
            print(f"    {_cell_label(a['requirement_id']):<34} {str(a.get('value', ''))[:100]}")

    # --- set aside -------------------------------------------------------------
    steers = [f for f in facts if f["kind"] == "suspended_constraint"]
    print(f"  \x1b[1mset aside this session ({len(steers)})\x1b[0m")
    for f in steers:
        v = f["value"]
        name = by_uid.get(v.get("uid"), {}).get("name", v.get("uid"))
        note = f"  note={v['note']}" if v.get("note") else ""
        print(f"    {name}{note}")

    # --- what was in force -----------------------------------------------------
    unanchored = sum(1 for r in rules if not r.get("anchors"))
    musts = sum(1 for r in rules if r.get("necessity") == "must")
    print(
        f"  in force: {len(rules)} rules ({musts} must, {unanchored} unanchored); "
        f"held back by day type: {snap.get('suspended_constraint_count', '?')}"
    )

    # --- the human half --------------------------------------------------------
    print("  \x1b[2mmissed:        \n  scanned:       (count `rules panel opened session_key=" + key + "` in the bot log)\n  wrong:         \n  sprint-shaped: \x1b[0m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    ap.add_argument("--day")
    ap.add_argument("--latest", type=int, default=3)
    ns = ap.parse_args()
    if ns.key:
        rows = _rows("where session_key = ?", (ns.key,))
    elif ns.day:
        rows = _rows("where planning_date = ?", (ns.day,))
    else:
        rows = _rows("where planning_date is not null", ())[: ns.latest]
    if not rows:
        raise SystemExit("no session matched")
    for row in rows:
        record(*row)


if __name__ == "__main__":
    main()

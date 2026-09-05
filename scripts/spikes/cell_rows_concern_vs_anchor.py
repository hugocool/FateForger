"""Spike: gap cells per CONCERN (a) vs per ANCHOR (b). Same fixture, same criteria, n draws each.

Reports: cells, calls, p50 latency, tokens, uncovered cells with cross-draw agreement,
and one generated probe per approach for its top uncovered cell.
Reads a throwaway copy of the store. Nothing is written anywhere.
"""
import asyncio, json, os, statistics, sys, time
from collections import Counter, defaultdict
from datetime import date
import httpx
from memory.anchor_store import AnchorStore
from memory.constraint_store import ConstraintStore
from memory.read_api import get_active_constraints

DB, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
env = {}
for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"').strip("'")
KEY, BASE = env["OPENROUTER_API_KEY"], env["OPENROUTER_BASE_URL"].rstrip("/")
MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL_FLASH") or "openai/gpt-oss-120b:nitro"
DAY = date(2026, 9, 8)  # a working Tuesday
SAID = 'The user said this session: "deep work in the morning, gym at 18:00."'

CONCERNS = {
    "bounded": "How the day is bounded: when it starts and ends, what frames it.",
    "fixed": "What is fixed: events, appointments, arrivals that do not move.",
    "movement": "Movement and transitions: commutes, travel, the gaps between fixed things.",
    "body": "Body: food, sleep, energy, exercise; the physical constraints on attention.",
    "fragile": "Fragile intentions: the things that only happen if protected; the reason a planner exists.",
    "not_today": "What today is not: suspensions, rules that usually hold and do not today.",
}
CRITERIA = {
    "tacit_assumptions": "Are the assumptions behind what is on record justified for this day, or are they unstated?",
    "alternatives": "Have alternatives been considered, i.e. what happens if a rule here cannot hold today?",
    "unclear": "Is anything here ambiguous or underspecified for placing it on today's timeline?",
    "contradictory": "Do any of the statements or rules here contradict each other, or the user's request?",
    "tacit_knowledge": "Is there knowledge only the user has (durations, arrivals, energy) that is unstated and the planner needs?",
}

# ---- fixture ----
cs, an = ConstraintStore(DB), AnchorStore(DB)
views = get_active_constraints(cs, DAY, day_type="working")
name_of = {a.uid: a.name for a in an.all()}
by_anchor = defaultdict(list)
for v in views:
    names = [name_of[u] for u in an.anchors_for(v.uid)] or ["(unanchored)"]
    for nm in names:
        by_anchor[nm].append(f"[{v.necessity.value}] {v.name}: {v.description}")
print(f"fixture: {len(views)} active durable rules on {DAY} (working), {len(by_anchor)} anchor groups "
      f"({len(by_anchor.get('(unanchored)', []))} unanchored)\n")

SEM = asyncio.Semaphore(24)
async def call(client, system, user, schema_hint):
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\n\n" + schema_hint}],
            "reasoning": {"effort": "minimal"}, "response_format": {"type": "json_object"}}
    async with SEM:
        for attempt in range(4):
            t = time.perf_counter()
            try:
                r = await client.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {KEY}"}, json=body)
                r.raise_for_status(); b = r.json()
                if "choices" not in b: raise httpx.TransportError(str(b.get("error", b))[:100])
                return json.loads(b["choices"][0]["message"]["content"]), time.perf_counter() - t, b.get("usage", {})
            except (httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError) as exc:
                if attempt == 3: raise
                await asyncio.sleep(1.5 * (attempt + 1))

async def place(client):
    user = "CONCERNS:\n" + json.dumps(CONCERNS, indent=1) + "\n\nANCHORS with example rules:\n" + json.dumps(
        {k: v[:2] for k, v in by_anchor.items() if k != "(unanchored)"}, indent=1)
    out, _, _ = await call(client, "Place each anchor under exactly one concern key. Answer a JSON object anchor->concern.", user, "")
    return out

CLASSIFY_SYS = ("You audit an elicitation conversation for a personal day planner, before the day is planned. "
    "Decide, for ONE criterion about ONE topic, whether the conversation so far settles it. Base the decision only on what "
    "is on record and what the user said; do not invent concerns never raised. "
    "status: 'covered' = settled for this day; 'uncovered' = a good coach would ask before planning; "
    "'not_applicable' = nothing here to have this about.")
def classify_user(topic_label, rules, crit_key):
    return (f"TOPIC: {topic_label}\nRULES ON RECORD (grouped, [must]/[should]):\n" + ("\n".join(rules) if rules else "(none)") +
            f"\n\n{SAID}\n\nCRITERION: {CRITERIA[crit_key]}")
SCHEMA = 'Answer JSON: {"status": "covered"|"uncovered"|"not_applicable", "why": "<at most 15 words>"}'

async def run(client, rows):
    """rows: list of (row_key, topic_label, rules). One draw = one parallel batch over all cells."""
    async def one():
        cells = [(rk, ck) for rk, _, _ in rows for ck in CRITERIA]
        res = await asyncio.gather(*[call(client, CLASSIFY_SYS, classify_user(lbl, rules, ck), SCHEMA)
                                     for rk, lbl, rules in rows for ck in CRITERIA])
        return {c: r for c, r in zip(cells, res)}
    draws = await asyncio.gather(*[one() for _ in range(N)])
    return draws

def report(label, rows, draws):
    cells = [(rk, ck) for rk, _, _ in rows for ck in CRITERIA]
    lat = [d[c][1] for d in draws for c in cells]; tok = sum(d[c][2].get("total_tokens", 0) for d in draws for c in cells) / N
    print(f"== {label}: {len(rows)} rows x {len(CRITERIA)} = {len(cells)} cells/draw, p50 {statistics.median(lat):.2f}s, "
          f"~{int(tok)} tokens/draw")
    unc = []
    for c in cells:
        votes = Counter(d[c][0].get("status", "NON-SCHEMA") for d in draws)
        st, k = votes.most_common(1)[0]
        if st == "uncovered":
            why = next(d[c][0].get("why", "") for d in draws if d[c][0].get("status") == "uncovered")
            unc.append((c, k, why))
    unanimous = sum(1 for c in cells if len(Counter(d[c][0].get("status") for d in draws)) == 1)
    print(f"   unanimous cells {unanimous}/{len(cells)}; modal-uncovered {len(unc)}")
    for (rk, ck), k, why in sorted(unc, key=lambda x: -x[1]):
        print(f"   {k}/{N}  {rk:<22} {ck:<18} {why}")
    return unc

GEN_SYS = ("Write ONE follow-up question a good planning coach would ask now. Base it only on what the user has said and what is on record. "
    "It must be: specific to this person and topic, short, no jargon, not technical, not asking for a solution, about one kind of thing, "
    "not open to several readings, not vague. Answer JSON {\"question\": ..., \"options\": [<=4 short closed answers, or empty]}")

async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        placement = await place(client)
        rows_a = []
        for ck, desc in CONCERNS.items():
            rules = [r for a, rs in by_anchor.items() if a != "(unanchored)" and placement.get(a) == ck for r in [f"({a}) " + x for x in rs]]
            rows_a.append((ck, desc, rules))
        rows_a.append(("unplaced", "Rules under no concern.", [r for a, rs in by_anchor.items() if a == "(unanchored)" or a not in placement for r in rs]))
        rows_b = [(a, f"the user's rules about '{a}'", rs) for a, rs in by_anchor.items()]
        da, db = await asyncio.gather(run(client, rows_a), run(client, rows_b))
        ua = report("(a) per concern", rows_a, da); print(); ub = report("(b) per anchor", rows_b, db); print()
        for label, unc, rows in (("(a)", ua, rows_a), ("(b)", ub, rows_b)):
            if not unc: continue
            (rk, ck), _, why = unc[0]
            rules = next(rs for k, _, rs in rows if k == rk)
            q, _, _ = await call(client, GEN_SYS, classify_user(rk, rules, ck) + f"\nGAP: {why}", "")
            print(f"probe {label} for {rk}/{ck}: {q}")

asyncio.run(main())

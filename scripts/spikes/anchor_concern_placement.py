"""Spike: can a model place every anchor under one concern of the floor, robustly?

n draws, parallel. Reports per-anchor modal concern and agreement rate.
Reads anchor names + two sample constraint names from the live store (read-only).
"""
import asyncio, json, os, sqlite3, sys
from collections import Counter
import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
env = {}
for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"').strip("'")
KEY, BASE = env["OPENROUTER_API_KEY"], env["OPENROUTER_BASE_URL"].rstrip("/")
MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL_FLASH") or "openai/gpt-oss-120b:nitro"

CONCERNS = {
    "bounded": "How the day is bounded — when it starts and ends, what frames it.",
    "fixed": "What is fixed — events, appointments, arrivals that do not move.",
    "movement": "Movement and transitions — commutes, travel, the gaps between fixed things.",
    "body": "Body — food, sleep, energy, exercise; the physical constraints on attention.",
    "fragile": "Fragile intentions — the things that only happen if protected; the reason a planner exists.",
    "not_today": "What today is not — suspensions: rules that usually hold and do not today.",
}

db = sqlite3.connect("file:data/memory.db?mode=ro", uri=True)
rows = db.execute("""
  select a.name, group_concat(c.name, ' ;; ') from anchors a
  join constraint_anchors ca on ca.anchor_uid=a.uid
  join constraints c on c.uid=ca.constraint_uid
  where c.status!='retracted' and c.uid in
    (select constraint_uid from constraint_anchors x where x.anchor_uid=a.uid limit 2)
  group by a.uid order by a.name""").fetchall()
anchors = [{"anchor": n, "example_rules": ex} for n, ex in rows]

SYSTEM = (
  "You are typing categories for a personal day-planner. Each ANCHOR is a thing the user "
  "has stated rules about. Place each anchor under exactly one CONCERN, or 'none' if no "
  "concern fits. Decide from what the anchor is, using the example rules only as context. "
  "Answer with a JSON object mapping every anchor name to one concern key."
)
USER = "CONCERNS:\n" + json.dumps(CONCERNS, indent=1) + "\n\nANCHORS:\n" + json.dumps(anchors, indent=1)

async def draw(client):
    r = await client.post(f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": MODEL,
              "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
              "reasoning": {"effort": "minimal"},
              "response_format": {"type": "json_object"}})
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
        draws = await asyncio.gather(*[draw(client) for _ in range(N)])
    keys = set(CONCERNS) | {"none"}
    agree_all = 0
    print(f"{'anchor':<26}{'mode':<12}{'agree':>6}  spread")
    for a in anchors:
        votes = Counter(str(d.get(a["anchor"], "MISSING")).strip() for d in draws)
        mode, k = votes.most_common(1)[0]
        bad = [v for v in votes if v not in keys]
        agree_all += k == N
        print(f"{a['anchor']:<26}{mode:<12}{k}/{N:<3}  {dict(votes)}{'  NON-SCHEMA:'+str(bad) if bad else ''}")
    print(f"\nunanimous anchors: {agree_all}/{len(anchors)}")
    dist = Counter(d.get(a['anchor']) for d in draws for a in anchors)
    print("overall placement distribution:", dict(dist))

asyncio.run(main())

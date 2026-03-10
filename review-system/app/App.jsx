import { useState, useRef, useEffect } from "react";

// ─── Design tokens ─────────────────────────────────────────────────────────
const C = {
  bg: "#080810", surface: "#0f0f1a", surface2: "#14141f", surface3: "#1a1a28",
  border: "#1e1e32", borderHover: "#2e2e48",
  accent: "#6ee7b7", accentDim: "#0a2018", accentGlow: "#6ee7b740",
  blue: "#7dd3fc", blueDim: "#0a1e2e",
  purple: "#c4b5fd", purpleDim: "#16123a",
  yellow: "#fde68a", yellowDim: "#1e1a08",
  red: "#fca5a5", redDim: "#200a0a",
  text: "#e2e8f0", textMid: "#94a3b8", textDim: "#475569",
  white: "#f8fafc",
};

const FONT = "'JetBrains Mono', 'Fira Code', monospace";

// ─── Shared helpers ─────────────────────────────────────────────────────────
const css = (s) => s;

function Spinner({ size = 14, color = C.accent }) {
  return (
    <div style={{
      width: size, height: size, border: `2px solid ${color}30`,
      borderTopColor: color, borderRadius: "50%",
      animation: "spin 0.7s linear infinite", flexShrink: 0,
    }} />
  );
}

function Tag({ label, color }) {
  return (
    <span style={{
      fontSize: 10, padding: "2px 8px", letterSpacing: "0.08em", fontWeight: 700,
      background: color + "18", color, border: `1px solid ${color}35`, borderRadius: 4,
    }}>{label}</span>
  );
}

function StatusPill({ ok, label }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11,
      padding: "3px 10px", borderRadius: 20,
      background: ok ? C.accentDim : C.redDim,
      color: ok ? C.accent : C.red,
      border: `1px solid ${ok ? C.accent : C.red}30`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: ok ? C.accent : C.red }} />
      {label}
    </span>
  );
}

// ─── Claude API caller ──────────────────────────────────────────────────────
async function callClaude({ notionToken, systemPrompt, messages, maxTokens = 1000 }) {
  const body = {
    model: "claude-sonnet-4-20250514",
    max_tokens: maxTokens,
    system: systemPrompt,
    messages,
    ...(notionToken ? {
      mcp_servers: [{
        type: "url", url: "https://mcp.notion.com/mcp",
        name: "notion", authorization_token: notionToken,
      }]
    } : {}),
  };
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error.message);
  return data;
}

function extractText(data) {
  return (data.content || []).filter(b => b.type === "text").map(b => b.text).join("\n").trim();
}

function parseJSON(text) {
  const clean = text.replace(/```json|```/g, "").trim();
  return JSON.parse(clean);
}

// ─── Setup Wizard ───────────────────────────────────────────────────────────
const STEPS = ["Token", "Page", "Databases", "Ready"];

function StepIndicator({ current }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 0, marginBottom: 40 }}>
      {STEPS.map((s, i) => (
        <div key={s} style={{ display: "flex", alignItems: "center" }}>
          <div style={{
            display: "flex", flexDirection: "column", alignItems: "center", gap: 6,
          }}>
            <div style={{
              width: 28, height: 28, borderRadius: "50%", display: "flex",
              alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700,
              background: i < current ? C.accent : i === current ? C.accentDim : C.surface3,
              color: i < current ? C.bg : i === current ? C.accent : C.textDim,
              border: `2px solid ${i < current ? C.accent : i === current ? C.accent : C.border}`,
              transition: "all 0.3s",
            }}>
              {i < current ? "✓" : i + 1}
            </div>
            <span style={{ fontSize: 10, color: i === current ? C.accent : C.textDim, letterSpacing: "0.1em" }}>
              {s.toUpperCase()}
            </span>
          </div>
          {i < STEPS.length - 1 && (
            <div style={{
              width: 60, height: 2, margin: "0 4px", marginBottom: 22,
              background: i < current ? C.accent : C.border, transition: "background 0.3s",
            }} />
          )}
        </div>
      ))}
    </div>
  );
}

function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(0);
  const [token, setToken] = useState("");
  const [pageInput, setPageInput] = useState("");
  const [pageInfo, setPageInfo] = useState(null); // { id, title }
  const [dbStatus, setDbStatus] = useState(null); // { reviews: {exists, id}, outcomes: {exists, id} }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showToken, setShowToken] = useState(false);

  const extractPageId = (input) => {
    const match = input.match(/[a-f0-9]{8}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{4}-?[a-f0-9]{12}/i)
      || input.match(/([a-f0-9]{32})/i);
    if (match) return match[0].replace(/-/g, "");
    return input.trim();
  };

  const testToken = async () => {
    if (!token.trim()) return;
    setLoading(true); setError(null);
    try {
      const data = await callClaude({
        notionToken: token.trim(),
        systemPrompt: "You are a connection tester. Use Notion MCP to search for any page. Return ONLY valid JSON: {\"ok\": true, \"workspace\": \"workspace name or 'connected'\"} or {\"ok\": false, \"error\": \"reason\"}. No markdown.",
        messages: [{ role: "user", content: "Test Notion connection. Search for any single page and confirm access." }],
      });
      const result = parseJSON(extractText(data));
      if (result.ok) setStep(1);
      else setError(result.error || "Connection failed");
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const verifyPage = async () => {
    if (!pageInput.trim()) return;
    setLoading(true); setError(null);
    const pageId = extractPageId(pageInput);
    try {
      const data = await callClaude({
        notionToken: token.trim(),
        systemPrompt: "You are a page verifier. Use Notion MCP to fetch the given page. Return ONLY valid JSON: {\"ok\": true, \"title\": \"page title\", \"id\": \"page-id\"} or {\"ok\": false, \"error\": \"reason\"}. No markdown.",
        messages: [{ role: "user", content: `Fetch Notion page with ID: ${pageId}. Return its title and confirm it exists.` }],
      });
      const result = parseJSON(extractText(data));
      if (result.ok) { setPageInfo({ id: pageId, title: result.title }); setStep(2); }
      else setError(result.error || "Page not found — check the URL and that the integration has access.");
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const checkDbs = async () => {
    setLoading(true); setError(null);
    try {
      const data = await callClaude({
        notionToken: token.trim(),
        systemPrompt: `You are a database checker. Use Notion MCP to search for databases named "Weekly Reviews" and "Outcomes" under page ID: ${pageInfo.id}. Return ONLY valid JSON: {"reviews": {"exists": bool, "id": "db-id or null"}, "outcomes": {"exists": bool, "id": "db-id or null"}}. No markdown.`,
        messages: [{ role: "user", content: `Search for "Weekly Reviews" and "Outcomes" databases under page ${pageInfo.id}.` }],
      });
      const result = parseJSON(extractText(data));
      setDbStatus(result);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const createDbs = async () => {
    setLoading(true); setError(null);
    try {
      const data = await callClaude({
        notionToken: token.trim(),
        maxTokens: 2000,
        systemPrompt: `You are a database creator. Use Notion MCP to create the two databases described below under parent page ID: ${pageInfo.id}. After creating both, return ONLY valid JSON: {"reviews_id": "db-id", "outcomes_id": "db-id"}. No markdown.

DATABASE 1: "Weekly Reviews"
Schema: week (date), intention (rich_text), wip_count (number), themes (rich_text), failure_looks_like (rich_text), thursday_signal (rich_text), clarity_gaps (rich_text), timebox_directives (rich_text), scrum_directives (rich_text)

DATABASE 2: "Outcomes"  
Schema: title (title), dod (rich_text), priority (select: Must/Support), status (select: Hit/Partial/Miss), ticket (url)
Note: Add a relation to Weekly Reviews after both are created.`,
        messages: [{ role: "user", content: `Create both databases under page ${pageInfo.id} and return their IDs.` }],
      });
      const result = parseJSON(extractText(data));
      setDbStatus({ reviews: { exists: true, id: result.reviews_id }, outcomes: { exists: true, id: result.outcomes_id } });
      setStep(3);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const finish = () => {
    onComplete({
      notionToken: token.trim(),
      pageId: pageInfo.id,
      pageName: pageInfo.title,
      weeklyReviewsDbId: dbStatus.reviews.id,
      outcomesDbId: dbStatus.outcomes.id,
    });
  };

  const inputStyle = {
    width: "100%", background: C.surface3, border: `1px solid ${C.border}`,
    borderRadius: 8, padding: "12px 14px", fontSize: 13, color: C.text,
    fontFamily: FONT, outline: "none", transition: "border-color 0.15s",
  };
  const btnStyle = (active) => ({
    width: "100%", background: active ? C.accent : C.accentDim,
    color: active ? C.bg : C.accent + "60", border: `1px solid ${active ? C.accent : C.accentDim}`,
    borderRadius: 8, padding: "12px", cursor: active ? "pointer" : "not-allowed",
    fontSize: 13, fontWeight: 700, fontFamily: FONT, transition: "all 0.15s",
    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
  });

  return (
    <div style={{ background: C.bg, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: FONT, padding: 24 }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
        * { box-sizing: border-box; }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        input:focus { border-color: ${C.accent} !important; }
      `}</style>

      <div style={{ width: "100%", maxWidth: 520, animation: "fadeIn 0.3s ease" }}>
        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 40 }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.25em", marginBottom: 10 }}>REVIEW SYSTEM</div>
          <div style={{ fontSize: 26, fontWeight: 700, color: C.white, letterSpacing: "-0.02em" }}>Setup Wizard</div>
          <div style={{ fontSize: 13, color: C.textMid, marginTop: 8 }}>Connect Notion → verify page → initialize databases</div>
        </div>

        <StepIndicator current={step} />

        {/* Step cards */}
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: "28px 32px" }}>

          {/* Step 0: Token */}
          {step === 0 && (
            <div style={{ animation: "fadeIn 0.2s ease" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.white, marginBottom: 6 }}>Notion Integration Token</div>
              <div style={{ fontSize: 12, color: C.textMid, marginBottom: 20, lineHeight: 1.6 }}>
                Get this from <span style={{ color: C.blue }}>notion.so → Settings → Connections → Develop or manage integrations → New integration</span>. Copy the Internal Integration Secret.
              </div>
              <div style={{ position: "relative", marginBottom: 12 }}>
                <input
                  type={showToken ? "text" : "password"}
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && testToken()}
                  placeholder="ntn_xxxxxxxxxxxxxxxx"
                  style={inputStyle}
                />
                <button onClick={() => setShowToken(!showToken)} style={{
                  position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)",
                  background: "none", border: "none", color: C.textDim, cursor: "pointer", fontSize: 12, fontFamily: FONT,
                }}>
                  {showToken ? "hide" : "show"}
                </button>
              </div>
              <div style={{ fontSize: 11, color: C.textDim, marginBottom: 16, display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: C.yellow }}>⚠</span> Token is stored in memory only — cleared when you close this tab.
              </div>
              {error && <div style={{ fontSize: 12, color: C.red, marginBottom: 12, padding: "8px 12px", background: C.redDim, borderRadius: 6 }}>{error}</div>}
              <button onClick={testToken} disabled={loading || !token.trim()} style={btnStyle(!loading && !!token.trim())}>
                {loading ? <><Spinner /> Testing connection…</> : "Test & Continue →"}
              </button>
            </div>
          )}

          {/* Step 1: Page */}
          {step === 1 && (
            <div style={{ animation: "fadeIn 0.2s ease" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.white, marginBottom: 6 }}>Parent Page</div>
              <div style={{ fontSize: 12, color: C.textMid, marginBottom: 20, lineHeight: 1.6 }}>
                The Weekly Reviews and Outcomes databases will be created inside this page. Paste a Notion page URL or ID. Make sure your integration has access to it (page → … → Connections → add integration).
              </div>
              <input
                value={pageInput}
                onChange={e => setPageInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && verifyPage()}
                placeholder="https://notion.so/Your-Page-abc123 or page ID"
                style={{ ...inputStyle, marginBottom: 12 }}
              />
              {error && <div style={{ fontSize: 12, color: C.red, marginBottom: 12, padding: "8px 12px", background: C.redDim, borderRadius: 6 }}>{error}</div>}
              <button onClick={verifyPage} disabled={loading || !pageInput.trim()} style={btnStyle(!loading && !!pageInput.trim())}>
                {loading ? <><Spinner /> Verifying page…</> : "Verify Page →"}
              </button>
            </div>
          )}

          {/* Step 2: Databases */}
          {step === 2 && (
            <div style={{ animation: "fadeIn 0.2s ease" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.white, marginBottom: 6 }}>Initialize Databases</div>
              <div style={{ fontSize: 12, color: C.textMid, marginBottom: 20, lineHeight: 1.6 }}>
                Checking for <span style={{ color: C.accent }}>Weekly Reviews</span> and <span style={{ color: C.purple }}>Outcomes</span> databases under <span style={{ color: C.blue }}>{pageInfo?.title}</span>.
              </div>

              {!dbStatus && (
                <button onClick={checkDbs} disabled={loading} style={btnStyle(!loading)}>
                  {loading ? <><Spinner /> Scanning page…</> : "Check for existing DBs"}
                </button>
              )}

              {dbStatus && (
                <div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
                    {[["Weekly Reviews", dbStatus.reviews], ["Outcomes", dbStatus.outcomes]].map(([name, db]) => (
                      <div key={name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px", background: C.surface3, borderRadius: 8, border: `1px solid ${C.border}` }}>
                        <span style={{ fontSize: 13, color: C.text }}>{name}</span>
                        <StatusPill ok={db.exists} label={db.exists ? (db.id ? db.id.slice(0, 8) + "…" : "found") : "missing"} />
                      </div>
                    ))}
                  </div>

                  {(!dbStatus.reviews.exists || !dbStatus.outcomes.exists) && (
                    <>
                      {error && <div style={{ fontSize: 12, color: C.red, marginBottom: 12, padding: "8px 12px", background: C.redDim, borderRadius: 6 }}>{error}</div>}
                      <button onClick={createDbs} disabled={loading} style={btnStyle(!loading)}>
                        {loading ? <><Spinner /> Creating databases…</> : "Create Missing Databases →"}
                      </button>
                      <div style={{ fontSize: 11, color: C.textDim, marginTop: 10, lineHeight: 1.6 }}>
                        If creation fails, run <span style={{ color: C.blue }}>notion_schema/init_db.py</span> locally and paste the DB IDs below.
                      </div>
                    </>
                  )}

                  {dbStatus.reviews.exists && dbStatus.outcomes.exists && (
                    <button onClick={() => setStep(3)} style={btnStyle(true)}>
                      Continue →
                    </button>
                  )}
                </div>
              )}

              {error && !dbStatus && <div style={{ fontSize: 12, color: C.red, marginTop: 12, padding: "8px 12px", background: C.redDim, borderRadius: 6 }}>{error}</div>}
            </div>
          )}

          {/* Step 3: Ready */}
          {step === 3 && (
            <div style={{ animation: "fadeIn 0.2s ease" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.white, marginBottom: 20 }}>Ready</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24 }}>
                {[
                  ["Notion", "Connected", C.accent],
                  ["Page", pageInfo?.title || pageInfo?.id, C.blue],
                  ["Weekly Reviews DB", dbStatus?.reviews?.id?.slice(0, 16) + "…", C.accent],
                  ["Outcomes DB", dbStatus?.outcomes?.id?.slice(0, 16) + "…", C.purple],
                ].map(([label, val, color]) => (
                  <div key={label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", background: C.surface3, borderRadius: 6 }}>
                    <span style={{ fontSize: 12, color: C.textMid }}>{label}</span>
                    <span style={{ fontSize: 12, color }}>{val}</span>
                  </div>
                ))}
              </div>
              <button onClick={finish} style={btnStyle(true)}>Open Review System →</button>
            </div>
          )}
        </div>

        <div style={{ textAlign: "center", marginTop: 20, fontSize: 11, color: C.textDim }}>
          Token stored in session memory only · Never sent to any server except Notion + Anthropic APIs
        </div>
      </div>
    </div>
  );
}

// ─── Architecture views (compact) ──────────────────────────────────────────
const LAYERS = [
  { id: "agents", label: "AGENT LAYER", color: C.purple, dim: C.purpleDim, desc: "Portable — swap framework, keep prompt",
    runtimes: ["AutoGen", "Claude Desktop", "Standalone"],
    components: [
      { name: "review_runner.md", sub: "SKILL.md / mcp://review/guidelines", notes: ["Socratic gating", "Phase sequencing", "Extraction-only", "Incremental writes"] },
      { name: "pattern_analysis.md", sub: "Same resource, narrower task", notes: ["Hit-rate across weeks", "Recurring themes", "Clarity gap history"] },
    ]},
  { id: "mcp", label: "MCP SERVER", color: C.yellow, dim: C.yellowDim, desc: "Context Triad: Tools + Resources + Prompts",
    runtimes: ["Docker", "stdio", "SSE"],
    components: [
      { name: "Tools", sub: "8 CRUD operations", notes: ["get_last_review", "get_reviews", "get_outcomes", "create_review", "patch_review_field", "append_phase_content", "create_outcome", "update_outcome_status"] },
      { name: "Resource", sub: "mcp://review/guidelines", notes: ["Serves SKILL.md at runtime", "Agent reads before acting", "Self-documenting capability"] },
      { name: "Prompt", sub: "review_session template", notes: ["Bootstraps reasoning chain", "Pre-loads phase structure", "Used by McpWorkbench"] },
    ]},
  { id: "tools", label: "TOOL LAYER", color: C.blue, dim: C.blueDim, desc: "Stateless — no review logic",
    runtimes: [],
    components: [
      { name: "tools/read.py", sub: "Read operations", notes: ["get_last_review()", "get_reviews(n)", "get_outcomes(review_id)"] },
      { name: "tools/write.py", sub: "Incremental writes", notes: ["create_review()", "patch_review_field()", "append_phase_content()", "create_outcome()", "update_outcome_status()"] },
    ]},
  { id: "orm", label: "ORM LAYER", color: C.accent, dim: C.accentDim, desc: "ultimate-notion — typed models",
    runtimes: [],
    components: [
      { name: "models/weekly_review.py", sub: "WeeklyReview(Page)", notes: ["week, intention, wip_count", "themes, failure_looks_like", "thursday_signal, clarity_gaps", "timebox_directives, scrum_directives"] },
      { name: "models/outcome.py", sub: "Outcome(Page)", notes: ["title, dod, priority", "status, review, ticket"] },
    ]},
  { id: "notion", label: "STORAGE", color: C.red, dim: C.redDim, desc: "Notion — two DBs",
    runtimes: [],
    components: [
      { name: "Weekly Reviews DB", sub: "1 row per week", notes: ["Page body: phase narratives", "Properties: queryable fields"] },
      { name: "Outcomes DB", sub: "N rows per week", notes: ["Relation → Weekly Reviews", "Hit-rate queryable"] },
    ]},
];

function ArchitectureView() {
  const [open, setOpen] = useState(null);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {LAYERS.map((L, i) => (
          <div key={L.id}>
            <div onClick={() => setOpen(open === L.id ? null : L.id)}
              style={{ background: open === L.id ? L.dim : C.surface, border: `1px solid ${open === L.id ? L.color : C.border}`, borderRadius: 8, padding: "12px 16px", cursor: "pointer", transition: "all 0.15s" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 7, height: 7, borderRadius: "50%", background: L.color }} />
                <span style={{ fontSize: 10, fontWeight: 700, color: L.color, letterSpacing: "0.15em", marginRight: 8 }}>{L.label}</span>
                <span style={{ fontSize: 12, color: C.textDim, flex: 1 }}>{L.desc}</span>
                {L.runtimes.map(r => <Tag key={r} label={r} color={L.color} />)}
                <span style={{ color: C.textDim, fontSize: 13 }}>{open === L.id ? "−" : "+"}</span>
              </div>
              {open === L.id && (
                <div style={{ display: "grid", gridTemplateColumns: `repeat(${L.components.length}, 1fr)`, gap: 8, marginTop: 12 }}>
                  {L.components.map(c => (
                    <div key={c.name} style={{ background: C.bg, borderRadius: 6, padding: "10px 12px", border: `1px solid ${L.color}20` }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: L.color }}>{c.name}</div>
                      <div style={{ fontSize: 11, color: C.textDim, marginBottom: 8 }}>{c.sub}</div>
                      {c.notes.map(n => <div key={n} style={{ fontSize: 11, color: C.textMid, marginBottom: 3 }}>· {n}</div>)}
                    </div>
                  ))}
                </div>
              )}
            </div>
            {i < LAYERS.length - 1 && (
              <div style={{ display: "flex", alignItems: "center", padding: "2px 24px", gap: 8 }}>
                <div style={{ flex: 1, height: 1, background: LAYERS[i + 1].color, opacity: 0.2 }} />
                <span style={{ fontSize: 10, color: LAYERS[i + 1].color, opacity: 0.4 }}>↓</span>
              </div>
            )}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 10 }}>CONTEXT TRIAD</div>
          {[["Tools", "The muscles — 8 CRUD ops, stateless", C.blue], ["Resource", "The brain — mcp://review/guidelines served at runtime", C.yellow], ["Prompt", "The template — bootstraps agent reasoning via McpWorkbench", C.purple]].map(([t, d, col]) => (
            <div key={t} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: col }}>↳ {t}</div>
              <div style={{ fontSize: 11, color: C.textMid, marginTop: 3, lineHeight: 1.5 }}>{d}</div>
            </div>
          ))}
        </div>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 10 }}>RUNTIMES</div>
          {[["AutoGen McpWorkbench", "SSE → Docker container"], ["AutoGen StdioMCP", "stdio → local process"], ["Claude Desktop", "stdio → mcp config JSON"], ["This app", "Browser → Claude API + Notion MCP"]].map(([rt, desc]) => (
            <div key={rt} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, color: C.text }}>{rt}</div>
              <div style={{ fontSize: 11, color: C.textDim }}>{desc}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Live Data View ─────────────────────────────────────────────────────────
function LiveDataView({ config }) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const fetch_ = async () => {
    setLoading(true); setError(null);
    try {
      const res = await callClaude({
        notionToken: config.notionToken,
        maxTokens: 2000,
        systemPrompt: `Fetch data from these Notion DBs: Weekly Reviews (${config.weeklyReviewsDbId}) and Outcomes (${config.outcomesDbId}). Return ONLY valid JSON (no markdown): {"reviews": [{"id","week","intention","themes","wip_count","failure_looks_like","thursday_signal","timebox_directives","scrum_directives"}], "outcomes": [{"id","review_id","title","dod","priority","status"}]}. Max 10 reviews.`,
        messages: [{ role: "user", content: "Fetch the last 10 Weekly Reviews and all linked Outcomes. Return as JSON." }],
      });
      const parsed = parseJSON(extractText(res));
      setData(parsed);
      if (parsed.reviews?.length > 0) setSelected(parsed.reviews[0].id);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  if (!data && !loading && !error) return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 60, gap: 14 }}>
      <div style={{ fontSize: 28 }}>📋</div>
      <div style={{ fontSize: 13, color: C.textMid }}>Load your review history from Notion</div>
      <button onClick={fetch_} style={{ background: C.accentDim, border: `1px solid ${C.accent}`, color: C.accent, padding: "10px 24px", borderRadius: 6, cursor: "pointer", fontSize: 12, fontFamily: FONT, fontWeight: 700 }}>Load Reviews</button>
    </div>
  );

  if (loading) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 60, gap: 12, color: C.textMid, fontSize: 13 }}><Spinner /> Fetching from Notion via MCP…</div>;
  if (error) return (
    <div style={{ padding: 20 }}>
      <div style={{ background: C.redDim, border: `1px solid ${C.red}40`, borderRadius: 8, padding: 14, fontSize: 12, color: C.red, marginBottom: 12 }}>{error}</div>
      <button onClick={fetch_} style={{ background: C.surface, border: `1px solid ${C.border}`, color: C.textMid, padding: "8px 16px", borderRadius: 6, cursor: "pointer", fontSize: 12, fontFamily: FONT }}>Retry</button>
    </div>
  );
  if (!data?.reviews?.length) return (
    <div style={{ padding: 40, textAlign: "center" }}>
      <div style={{ fontSize: 13, color: C.textMid, marginBottom: 8 }}>No reviews yet.</div>
      <div style={{ fontSize: 12, color: C.textDim }}>Run your first review session in the Chat tab to create the first entry.</div>
    </div>
  );

  const rev = data.reviews.find(r => r.id === selected);
  const outcomes = data.outcomes?.filter(o => o.review_id === selected) || [];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 14, height: 500 }}>
      <div style={{ background: C.surface, borderRadius: 8, border: `1px solid ${C.border}`, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "9px 12px", borderBottom: `1px solid ${C.border}`, fontSize: 10, color: C.textDim, letterSpacing: "0.15em" }}>REVIEWS ({data.reviews.length})</div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {data.reviews.map(r => (
            <div key={r.id} onClick={() => setSelected(r.id)}
              style={{ padding: "9px 12px", cursor: "pointer", borderBottom: `1px solid ${C.border}`, background: selected === r.id ? C.accentDim : "transparent" }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: selected === r.id ? C.accent : C.text }}>{r.week || "—"}</div>
              <div style={{ fontSize: 11, color: C.textDim, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.intention || "No intention"}</div>
            </div>
          ))}
        </div>
        <div style={{ padding: "8px 12px", borderTop: `1px solid ${C.border}` }}>
          <button onClick={fetch_} style={{ width: "100%", background: "transparent", border: `1px solid ${C.border}`, color: C.textDim, padding: "5px", borderRadius: 5, cursor: "pointer", fontSize: 11, fontFamily: FONT }}>↻ Refresh</button>
        </div>
      </div>
      <div style={{ background: C.surface, borderRadius: 8, border: `1px solid ${C.border}`, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {rev ? (
          <>
            <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>{rev.week}</span>
              <span style={{ fontSize: 12, color: C.textMid, flex: 1 }}>{rev.intention}</span>
              {rev.wip_count && <Tag label={`WIP ${rev.wip_count}`} color={C.yellow} />}
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 8 }}>OUTCOMES ({outcomes.length})</div>
                {outcomes.length === 0 ? <div style={{ fontSize: 12, color: C.textDim, fontStyle: "italic" }}>No outcomes linked.</div>
                  : outcomes.map(o => (
                    <div key={o.id} style={{ background: C.surface2, borderRadius: 6, padding: "9px 12px", marginBottom: 7, border: `1px solid ${C.border}` }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 4 }}>
                        <span style={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0, background: o.status === "Hit" ? C.accent : o.status === "Partial" ? C.yellow : o.status === "Miss" ? C.red : C.textDim }} />
                        <span style={{ fontSize: 12, fontWeight: 600, color: C.text, flex: 1 }}>{o.title}</span>
                        {o.priority && <Tag label={o.priority} color={o.priority === "Must" ? C.accent : C.purple} />}
                        {o.status && <Tag label={o.status} color={o.status === "Hit" ? C.accent : o.status === "Partial" ? C.yellow : C.red} />}
                      </div>
                      <div style={{ fontSize: 11, color: C.textMid, paddingLeft: 14 }}>DoD: {o.dod || "—"}</div>
                    </div>
                  ))}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {[["Themes", rev.themes, C.accent], ["Failure looks like", rev.failure_looks_like, C.red], ["Thursday signal", rev.thursday_signal, C.yellow], ["Timebox directives", rev.timebox_directives, C.blue]].map(([label, val, col]) => (
                  <div key={label} style={{ background: C.surface2, borderRadius: 6, padding: "9px 12px", border: `1px solid ${C.border}` }}>
                    <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.1em", marginBottom: 5 }}>{label.toUpperCase()}</div>
                    <div style={{ fontSize: 12, color: val ? col : C.textDim, fontStyle: val ? "normal" : "italic" }}>{val || "Not set"}</div>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.textDim, fontSize: 13 }}>Select a review</div>}
      </div>
    </div>
  );
}

// ─── Chat View ───────────────────────────────────────────────────────────────
const SYSTEM_PROMPT = `You are the Review System assistant. You have access to the user's Notion workspace via MCP tools.

You can read Weekly Reviews and Outcomes databases, summarise patterns across weeks, surface insights about missed outcomes and recurring themes, and write or patch review data when asked.

When starting a review session:
1. Call get_last_review() to load last week's context
2. Call get_outcomes(review_id) to load last week's outcomes
3. Present outcomes for scoring (Hit/Partial/Miss) and call update_outcome_status for each
4. Call create_review(week_date) to start this week's row
5. Begin Phase 1 — extract, never suggest

Be concise. Reference specific data. Confirm before writing.`;

const SUGGESTIONS = [
  "Start my weekly review",
  "What was my must outcome last week?",
  "What patterns keep showing up?",
  "Summarise my outcome hit rate for the last 8 weeks",
];

function ChatView({ config }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const send = async (text) => {
    const content = text || input.trim();
    if (!content || loading) return;
    const userMsg = { role: "user", content };
    const newMsgs = [...msgs, userMsg];
    setMsgs(newMsgs);
    setInput("");
    setLoading(true);
    try {
      const data = await callClaude({
        notionToken: config.notionToken,
        systemPrompt: SYSTEM_PROMPT,
        messages: newMsgs,
        maxTokens: 1500,
      });
      setMsgs(prev => [...prev, { role: "assistant", content: extractText(data) }]);
    } catch (e) {
      setMsgs(prev => [...prev, { role: "assistant", content: `Error: ${e.message}` }]);
    }
    setLoading(false);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: 560 }}>
      <div style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        {msgs.length === 0 && (
          <div style={{ padding: "16px 0" }}>
            <div style={{ fontSize: 12, color: C.textMid, marginBottom: 10 }}>Quick starts:</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {SUGGESTIONS.map(s => (
                <div key={s} onClick={() => send(s)}
                  style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 14px", fontSize: 12, color: C.textMid, cursor: "pointer" }}>
                  {s}
                </div>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: "flex", gap: 8, justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            {m.role === "assistant" && (
              <div style={{ width: 26, height: 26, borderRadius: "50%", background: C.accentDim, border: `1px solid ${C.accent}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flexShrink: 0 }}>✦</div>
            )}
            <div style={{ maxWidth: "78%", background: m.role === "user" ? C.accentDim : C.surface, border: `1px solid ${m.role === "user" ? C.accent + "40" : C.border}`, borderRadius: m.role === "user" ? "12px 12px 4px 12px" : "12px 12px 12px 4px", padding: "9px 13px", fontSize: 13, color: m.role === "user" ? C.accent : C.text, lineHeight: 1.65, whiteSpace: "pre-wrap" }}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ display: "flex", gap: 8 }}>
            <div style={{ width: 26, height: 26, borderRadius: "50%", background: C.accentDim, border: `1px solid ${C.accent}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11 }}>✦</div>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: "12px 12px 12px 4px", padding: "10px 14px", display: "flex", gap: 5, alignItems: "center" }}>
              {[0, 1, 2].map(i => <div key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: C.accent, animation: `pulse 1.2s ease ${i * 0.2}s infinite` }} />)}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div style={{ padding: "10px 14px", borderTop: `1px solid ${C.border}`, display: "flex", gap: 8 }}>
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
          placeholder="Message the review agent…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          style={{ flex: 1, background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 13px", fontSize: 13, color: C.text, fontFamily: FONT, outline: "none", resize: "none", lineHeight: 1.5 }}
        />
        <button onClick={() => send()} disabled={loading || !input.trim()}
          style={{ background: loading || !input.trim() ? C.accentDim : C.accent, color: C.bg, border: "none", borderRadius: 8, padding: "0 18px", cursor: loading || !input.trim() ? "default" : "pointer", fontSize: 13, fontWeight: 700, fontFamily: FONT, alignSelf: "stretch" }}>
          Send
        </button>
      </div>
    </div>
  );
}

// ─── Session flow & file tree (compact) ─────────────────────────────────────
const SESSION_FLOW = [
  { phase: "Session Open", action: "get_last_review() → get_outcomes(id)", write: "update_outcome_status() × N\ncreate_review(week_date)" },
  { phase: "Phase 1 gate", action: "Extract wins, misses, themes", write: "patch(themes)\nappend_phase_content('reflect')" },
  { phase: "Phase 2 gate", action: "WIP count, staleness, balance", write: "patch(wip_count)\nappend_phase_content('board_scan')" },
  { phase: "Phase 3 gate", action: "Each outcome → binary DoD", write: "create_outcome(...) × N" },
  { phase: "Phase 4 gate", action: "Risks, pre-mortem, Thursday signal", write: "patch(failure_looks_like)\npatch(thursday_signal)" },
  { phase: "Phase 5 close", action: "Intention + directives extracted", write: "patch(intention)\npatch(timebox_directives)\npatch(scrum_directives)\npatch(clarity_gaps)" },
];

function SessionFlowView() {
  return (
    <div>
      <div style={{ background: C.surface, borderRadius: 8, border: `1px solid ${C.border}`, overflow: "hidden", marginBottom: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "160px 1fr 1fr", borderBottom: `1px solid ${C.border}` }}>
          {["Phase", "Agent reads / extracts", "Notion writes (immediate)"].map(h => (
            <div key={h} style={{ padding: "9px 14px", fontSize: 10, color: C.textDim, letterSpacing: "0.1em", fontWeight: 700 }}>{h}</div>
          ))}
        </div>
        {SESSION_FLOW.map((row, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "160px 1fr 1fr", borderBottom: i < SESSION_FLOW.length - 1 ? `1px solid ${C.border}` : "none" }}>
            <div style={{ padding: "11px 14px", borderRight: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: i === 0 ? C.purple : C.accent }}>{row.phase}</span>
            </div>
            <div style={{ padding: "11px 14px", fontSize: 11, color: C.textMid, borderRight: `1px solid ${C.border}`, lineHeight: 1.6 }}>{row.action}</div>
            <div style={{ padding: "11px 14px", fontSize: 11, color: C.blue, lineHeight: 1.8, whiteSpace: "pre" }}>{row.write}</div>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 8 }}>RESUMABILITY</div>
          <div style={{ fontSize: 12, color: C.textMid, lineHeight: 1.7 }}>
            Session breaks → Notion has partial state.<br />
            Agent reopens → reads existing row → detects populated phases → resumes from next empty phase.<br />
            <span style={{ color: C.yellow }}>State always in Notion, never only in context window.</span>
          </div>
        </div>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 8 }}>EXTRACTION RULES</div>
          {["Never suggest — only extract", "Gate doesn't advance until fully met", "Write immediately on gate met — never batch", "One question at a time", "Synthesize what you heard before advancing"].map(r => (
            <div key={r} style={{ fontSize: 11, color: C.textMid, marginBottom: 5 }}>· {r}</div>
          ))}
        </div>
      </div>
    </div>
  );
}

const FILE_TREE = `review-system/
├── mcp/
│   └── server.py         # FastMCP — Context Triad
│       ├── @tool          # 8 CRUD operations
│       ├── @resource      # mcp://review/guidelines
│       └── @prompt        # review_session template
├── models/
│   ├── weekly_review.py   # WeeklyReview(Page)
│   └── outcome.py         # Outcome(Page)
├── tools/
│   ├── read.py            # get_last_review, get_reviews, get_outcomes
│   └── write.py           # create_review, patch_review_field...
├── skills/
│   └── review_system/
│       └── SKILL.md       # = mcp://review/guidelines content
├── notion_schema/
│   └── init_db.py         # one-time DB creation fallback
├── Dockerfile
├── docker-compose.yml
└── .env`;

function FilesView() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <pre style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 18, fontSize: 12, lineHeight: 1.8, margin: 0, overflowX: "auto" }}>
        {FILE_TREE.split("\n").map((line, i) => {
          const isFile = line.includes(".py") || line.includes(".md") || line.includes(".yml") || line.includes(".env");
          const isDir = !isFile && (line.includes("/") || line.includes("├") || line.includes("└"));
          const isComment = line.includes("#");
          return (
            <span key={i} style={{ display: "block", color: isComment ? C.textDim : isFile ? C.blue : isDir ? C.accent : C.textDim }}>
              {line}
            </span>
          );
        })}
      </pre>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 10 }}>USAGE MODES</div>
          {[
            ["Docker + SSE", "python -m mcp.server --transport sse", C.accent, "AutoGen SSE, Claude Desktop URL"],
            ["stdio", "python -m mcp.server", C.blue, "AutoGen StdioMCP, Claude Desktop stdio"],
            ["Direct import", "from tools import read, write", C.purple, "AutoGen agent, no MCP overhead"],
          ].map(([mode, cmd, col, desc]) => (
            <div key={mode} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: col }}>{mode}</div>
              <code style={{ fontSize: 11, color: C.textMid, display: "block", marginTop: 2 }}>{cmd}</code>
              <div style={{ fontSize: 11, color: C.textDim, marginTop: 2 }}>{desc}</div>
            </div>
          ))}
        </div>
        <div style={{ background: C.surface, borderRadius: 8, padding: "14px 16px", border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.15em", marginBottom: 10 }}>MCPWORKBENCH PATTERN</div>
          <pre style={{ margin: 0, fontSize: 11, color: C.textMid, lineHeight: 1.7, overflowX: "auto" }}>{`async with McpWorkbench(params) as wb:
  # Discover tools
  tools = wb.as_tools()

  # Read the guidelines resource
  # Agent sees: mcp://review/guidelines
  # and loads SKILL.md before acting

  # Get reasoning template
  prompt = await wb.get_prompt(
    "review_session"
  )

  agent = AssistantAgent(
    tools=tools,
    system_message=prompt,
  )`}</pre>
        </div>
      </div>
    </div>
  );
}

// ─── Main App ────────────────────────────────────────────────────────────────
const TABS = [
  { id: "arch", label: "Architecture" },
  { id: "session", label: "Session Flow" },
  { id: "files", label: "File Tree" },
  { id: "data", label: "Live Data", live: true },
  { id: "chat", label: "Chat", live: true, color: C.purple },
];

export default function App() {
  const [config, setConfig] = useState(null);
  const [tab, setTab] = useState("arch");

  if (!config) return <SetupWizard onComplete={(c) => { setConfig(c); setTab("data"); }} />;

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: FONT, color: C.text, padding: "24px 20px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: ${C.surface}; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 2px; }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100%{opacity:0.25} 50%{opacity:1} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
        textarea:focus, input:focus { border-color: ${C.accent} !important; }
      `}</style>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 10, color: C.textDim, letterSpacing: "0.2em", marginBottom: 5 }}>REVIEW SYSTEM</div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: C.white, letterSpacing: "-0.02em" }}>Weekly Review</h1>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <StatusPill ok={true} label={config.pageName || "Connected"} />
          <button onClick={() => setConfig(null)}
            style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.textDim, padding: "5px 12px", borderRadius: 6, cursor: "pointer", fontSize: 11, fontFamily: FONT }}>
            ← Setup
          </button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 2, marginBottom: 20, borderBottom: `1px solid ${C.border}` }}>
        {TABS.map(t => (
          <div key={t.id} onClick={() => setTab(t.id)}
            style={{ padding: "7px 14px", fontSize: 12, cursor: "pointer", userSelect: "none", transition: "color 0.15s",
              color: tab === t.id ? (t.color || C.text) : C.textDim,
              borderBottom: `2px solid ${tab === t.id ? (t.color || C.text) : "transparent"}`,
              marginBottom: -1 }}>
            {t.label}
            {t.live && <span style={{ marginLeft: 5, fontSize: 9, padding: "1px 5px", background: t.color ? t.color + "18" : C.accentDim, color: t.color || C.accent, borderRadius: 3, fontWeight: 700 }}>LIVE</span>}
          </div>
        ))}
      </div>

      {tab === "arch" && <ArchitectureView />}
      {tab === "session" && <SessionFlowView />}
      {tab === "files" && <FilesView />}
      {tab === "data" && <LiveDataView config={config} />}
      {tab === "chat" && <ChatView config={config} />}
    </div>
  );
}

---
paths:
  - "src/memory/*.py"
  - "src/memory/**/*.py"
  - "tests/memory/*.py"
---

# The memory server

- **Run anything under it with `PYTHONPATH=src`.** It is not installed as a package, and the failure without it is a bare `ModuleNotFoundError` that looks like a missing dependency.
- **It imports nothing from `fateforger.*`** and must stay that way — it is an MCP server any host can drive, and FateForger is one host among several.
- **It owns no model.** No API key, no pin: it asks the connected host via MCP sampling, so the host's model governs quality. `OpenRouterJudge` remains for offline corpus work where there is no host to ask.
- Both transports subclass `PromptJudge`, which holds the prompt text and the parsing. **Never put a prompt in a transport subclass** — two ways to reach a model is two places a question can drift.
- **A sampling failure must stay loud** — `SamplingUnavailable` and `SamplingDeclined` propagate out of `MemoryService.observe` (incident I9).
- **The read path never calls a model**: `get_active_constraints` is synchronous, arithmetic-only, and guarded by an AST test (incident I11). Turning a name into an anchor uid is a judgement, so it happens in `resolve_anchor_names`, not at read time.
- Call `get_active_constraints` **without** `anchor_uids` until the promotion gate (#140) lands: `anchor_edges` is deliberately unpopulated, so narrowing loses any rule whose own anchor is not among the seeds. `status` is likewise a constant — `LOCKED` is never emitted.
- Re-projection samples once per observation; it is an explicit call, never a request-path one.
- **`data/memory.db*` is gitignored and stays that way** (incident I2).

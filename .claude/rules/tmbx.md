---
paths:
  - "src/tmbx/*.py"
  - "src/tmbx/**/*.py"
---

# tmbx — the timebox models and the journal

- **The journal is written before the calendar.** An apply that fails journals the attempt and writes nothing to the calendar (`service.py`); the journal, not the calendar, is the history the rest of the system reads back.
- `TBEvent` / `TBPlan` are the **sole LLM-facing models** for timebox generation. All event types use the compact `ET` enum (`M`, `C`, `DW`, `SW`, `PR`, `H`, `R`, `BU`, `BG`).
- `TBPatch` uses typed domain ops (`ae`, `re`, `ue`, `me`, `ra`) — never generic JSON Patch. Inject `TBPatch.model_json_schema()` into the system prompt and parse the raw JSON text the model returns.
- **`output_content_type=TBPatch` is intentionally NOT used**, because `oneOf` from Pydantic discriminated unions breaks both OpenAI `response_format` and OpenRouter structured output on the hosts this was measured on. An agent "fixing" this to look cleaner re-breaks the patcher (incident I12).
- The import boundary is one-way and a test guards it: **tmbx never imports from `fateforger`**; the reverse is allowed (`tests/unit/tmbx/test_import_boundary.py`).

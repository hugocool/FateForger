# TRMNL Frontend for FateForger

A **5-minute refresh-aware** e-ink dashboard that displays productivity metrics from FateForger's timeboxing system.

## Philosophy: Truthful Time on E-ink

This dashboard is designed for **TRMNL X** (non-interactive, 5-minute refresh). Unlike traditional UIs, we don't pretend time is continuous:

- ❌ **NO live clocks** ("10:47" lies for 4 out of 5 minutes)
- ✅ **Bucket windows** ("10:45–10:50" sets accurate expectations)
- ✅ **Time ranges** ("40–45m left" instead of "43m")
- ✅ **Progress dots** (discrete 5-minute slices, not smooth bars)

## Architecture

```
┌─────────────────────────────────────────────────┐
│  FateForger Backend (Python)                    │
│  ├─ Google Calendar MCP  → planned blocks       │
│  ├─ TickTick/Notion MCP  → tasks                │
│  ├─ Toggl Integration    → tracked time         │
│  └─ Time Quantizer       → 5-min buckets        │
└──────────────────┬──────────────────────────────┘
                   │ Every 5 minutes
                   ↓
         ┌─────────────────────┐
         │  data.json payload  │
         │  (pre-computed)     │
         └──────────┬──────────┘
                    │
                    ↓
         ┌──────────────────────┐
         │  template.liquid     │
         │  (TRMNL Framework v2)│
         └──────────┬───────────┘
                    │
                    ↓
         ┌──────────────────────┐
         │  TRMNL Device        │
         │  (800x480, 1-bit)    │
         └──────────────────────┘
```

## Files

- **[data.json](data.json)**: Example payload (matches schema.json)
- **[schema.json](schema.json)**: JSON Schema for payload validation
- **[template.liquid](template.liquid)**: TRMNL Framework v2 template
- **[docker-compose.yml](docker-compose.yml)**: Local preview server
- **[AGENTS.md](AGENTS.md)**: AI agent instructions for development

## Quick Start

### 1. Launch Preview Server

```bash
cd src/trmnl_frontend
docker compose up
```

Or use VS Code task: **FateForger: TRMNL Preview**

### 2. View Dashboard

Open [http://localhost:4567](http://localhost:4567)

Toggle "E-ink" mode to see 1-bit rendering.

### 3. Edit Template

Modify [src/full.liquid](src/full.liquid) and the browser refreshes automatically (hot reload enabled).

Use [TRMNL Framework v2 components](https://usetrmnl.com/framework):
- `progress-bar` for metrics
- `progress-dots` for time slices
- `item` for task lists
- `grid` for layout

### 4. Update Data

Edit [src/data.json](src/data.json) to test different scenarios:

Edit [data.json](data.json) to test different scenarios:
- Different block types (DW, M, SW, H, R, BU, PR, BG)
- Various time ranges
- Task pipeline changes
- Micro-step progress

## Data Contract

The backend must provide a JSON payload matching [schema.json](schema.json).

### Required Top-Level Fields

```json
{
  "meta": {
    "today_label": "Tue 16 Jan",
    "now_window": "10:45–10:50",     // Current 5-min bucket
    "snapshot_local": "10:45",        // When data was generated
    "next_refresh_local": "10:50",   // When next refresh happens
    "refresh_minutes": 5
  },
  "day": {
    "start_local": "09:00",
    "end_local": "17:00",
    "current_slice": 22,              // 5-min slice # (0-based)
    "total_slices": 96,               // 8 hours = 96 slices
    "progress_pct": 22.9
  },
  "now": {
    "event_type": "DW",               // DW|M|SW|H|R|BU|PR|BG
    "block_title": "Product Strategy",
    "subtitle": "One Thing: Finalize MVP Spec",
    "starts_local": "09:00",
    "ends_local": "10:30",
    "remaining_range_label": "40–45m", // Range, not precise
    "bucket_hint": "Coarse time: you are in the 10:45–10:50 slice",
    "block_dots": ["filled", "filled", ..., "current", "empty", ...] // One per 5min
  },
  "next": { /* Next block */ },
  "metrics": {
    "deepwork": { "tracked_min": 75, "planned_min": 210, "progress_pct": 35.7, "label_right": "75m / 210m" },
    "tasks": { "done": 3, "total": 6, "progress_pct": 50, "label_right": "3/6 DONE" }
  },
  "pipeline": {
    "source_label": "TickTick",
    "items": [
      { "title": "Finalize MVP Spec", "status": "next", "tags": ["ONETHING"], "est_min": 60, "emphasis": 3 }
    ]
  },
  "microsteps": {
    "title": "MICRO-STEPS (ONE THING)",
    "items": [
      { "title": "Draft section 1", "status": "done" },
      { "title": "Draft section 2", "status": "doing" }
    ]
  }
}
```

### Backend Responsibilities

The Python backend (to be implemented) must:

1. **Quantize time** to 5-minute boundaries
2. **Compute block_dots** array (one per 5-min slice in current block)
3. **Calculate remaining_range_label** (min/max within bucket)
4. **Merge data sources**:
   - Google Calendar → `now`, `next`, `day.total_slices`
   - TickTick/Notion → `pipeline.items`
   - Toggl → `metrics.deepwork.tracked_min`

### Template Responsibilities

The Liquid template (already implemented) should:

- **Keep it dumb**: No time calculations
- **Just render**: Display pre-computed values
- **Use Framework v2**: Built-in components only

## Views

### Command View (Default)

**Two-pane layout (3/5 left, 2/5 right)**

**Left Pane:**
- Day anchor rail (where am I in the day?)
- NOW block (current focus)
- 5-minute progress dots (truthful time progression)
- UP NEXT block
- Snapshot/refresh footer

**Right Pane:**
- Deep Work metrics (tracked vs planned)
- Task velocity (done/total)
- Pipeline (upcoming tasks)
- Micro-steps (One Thing breakdown)

### Ledger View (Optional, Future)

Plan vs actual audit:
- Timeline of today's blocks
- Drift calculations
- Velocity metrics

## Event Type Taxonomy

| Code | Meaning | Example |
|------|---------|---------|
| `DW` | Deep Work | Product design, coding |
| `M`  | Meeting | Standup, 1:1s |
| `SW` | Shallow Work | Email, Slack, admin |
| `H`  | Habit | Exercise, meditation |
| `R`  | Recovery | Break, lunch |
| `BU` | Buffer | Unplanned flex time |
| `PR` | Personal | Errands, appointments |
| `BG` | Background | Low-priority tasks |

## Design Constraints

### E-ink Limitations
- **1-bit color** (black & white only, no grayscale)
- **No animations** (e-ink doesn't support them)
- **Slow refresh** (~2 seconds per update)

### 5-Minute Refresh Implications
- **No second/minute precision** (will appear frozen)
- **No continuous progress** (use discrete steps)
- **No "stuck clock" illusions** (show bucket windows)

### UI Anti-Patterns (FORBIDDEN)

❌ Live clocks ("10:47")  
❌ Precise remaining time ("43m left")  
❌ Smooth progress bars  
❌ Button affordances (no touch support)  
❌ Color gradients  
❌ Animations  

## Integration with FateForger

### Data Sources

1. **Google Calendar MCP** (`calendar-mcp` service)
   - Provides planned time blocks
   - Event types inferred from title prefixes
   - "One Thing" extracted from DW block descriptions

2. **TickTick/Notion MCP** (`ticktick-mcp`, `notion-mcp` services)
   - Task titles, statuses, estimates
   - Tags for categorization (ONETHING, DW, SW)
   - Merged into unified pipeline

3. **Toggl Integration** (via FateForger backend)
   - Tracked time entries
   - Mapped to event types (DW/SW/M)
   - Used for planned vs actual metrics

### Polling Endpoint (To Implement)

The FateForger backend should expose:

```
GET /api/trmnl/dashboard
```

Returns JSON payload matching [schema.json](schema.json).

Update frequency: Every 5 minutes (cron or APScheduler).

## Development Workflow

### Local Preview Mode

1. Edit [src/data.json](src/data.json) with mock scenarios
2. Edit [src/full.liquid](src/full.liquid) for layout changes
3. Run `docker compose up`
4. Preview at [http://localhost:4567](http://localhost:4567)
5. Toggle "E-ink" mode to verify 1-bit rendering

### Production Integration

1. Implement Python endpoint: `src/fateforger/api/trmnl_endpoint.py`
2. Add time quantization helpers: `src/fateforger/utils/time_bucket.py`
3. Merge MCP data sources
4. Deploy as TRMNL Private Plugin
5. Configure TRMNL device to poll endpoint

## Testing Scenarios

Update [data.json](data.json) to test:

### Scenario: Deep Work Block (Current)
```json
{
  "now": {
    "event_type": "DW",
    "block_title": "Feature Development",
    "subtitle": "One Thing: Complete user auth flow",
    "remaining_range_label": "85–90m",
    "block_dots": ["filled","filled","filled","current","empty","empty",...] 
  }
}
```

### Scenario: Meeting (Up Next)
```json
{
  "next": {
    "event_type": "M",
    "title": "Sprint Planning",
    "subtitle": "Review backlog",
    "starts_local": "14:00"
  }
}
```

### Scenario: Behind on Deep Work
```json
{
  "metrics": {
    "deepwork": {
      "tracked_min": 45,
      "planned_min": 180,
      "progress_pct": 25,
      "label_right": "45m / 180m"
    }
  }
}
```

### Scenario: Task Pipeline with One Thing
```json
{
  "pipeline": {
    "items": [
      { "title": "MVP Spec", "status": "next", "tags": ["ONETHING"], "emphasis": 3 },
      { "title": "Code review", "status": "next", "tags": ["DW"], "emphasis": 2 },
      { "title": "Email catch-up", "status": "later", "tags": ["SW"], "emphasis": 1 }
    ]
  }
}
```

## TRMNL Framework v2 Components Used

- **Title Bar**: Top-level metadata (title, instance)
- **Grid**: Two-column layout (`grid--cols-5`)
- **Progress Bar**: Metrics display (Deep Work, Tasks)
- **Progress Dots**: Time slice visualization (5-min steps)
- **Item**: Task/block cards with emphasis levels
- **Divider**: Visual separation
- **Flex**: Utility layout system
- **Labels**: Small metadata text
- **Overflow**: Automatic list truncation with "and X more"

See [TRMNL Framework v2 Docs](https://usetrmnl.com/framework) for details.

## Next Steps

1. ✅ Scaffold TRMNL frontend structure
2. ✅ Implement Command View template
3. ✅ Document data contract and schema
4. 🔲 Implement Python backend endpoint (`/api/trmnl/dashboard`)
5. 🔲 Implement time quantization utilities
6. 🔲 Integrate Google Calendar MCP queries
7. 🔲 Integrate TickTick/Notion MCP queries
8. 🔲 Integrate Toggl tracking data
9. 🔲 Deploy as TRMNL Private Plugin
10. 🔲 Configure refresh schedule (5-minute cron)

## References

- [TRMNL Framework v2](https://usetrmnl.com/framework)
- [TRMNL Private Plugins](https://help.usetrmnl.com/en/articles/9510536-private-plugins)
- [TRMNL Display Docs](https://usetrmnl.com/docs)
- [FateForger AGENTS.md](../../AGENTS.md)
- [Calendar MCP Guide](../../GOOGLE_CALENDAR_MCP_GUIDE.md)

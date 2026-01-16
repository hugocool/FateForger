# Agent Instructions: TRMNL Dashboard Development

## Environment Context
- **Platform**: TRMNL E-ink Display (800x480 resolution)
- **Language**: HTML, CSS, and Shopify Liquid
- **Framework**: TRMNL Framework v2 (https://usetrmnl.com/framework)
- **Constraints**: 1-bit color (Black and White only). No Grayscale. No Animations.
- **Critical**: 5-minute refresh cadence (non-interactive read-only display)

## Time Representation Rules (NON-NEGOTIABLE)

### The 5-Minute Truth Contract
1. **NEVER show live clocks** (e.g., "10:47" implies second-by-second updates)
2. **ALWAYS show time as buckets**: "10:45–10:50" (5-minute windows)
3. **ALWAYS show remaining time as ranges**: "40–45m" (not "43m")
4. **ALWAYS use progress dots for time visualization** (filled/current/empty states)
5. **ALWAYS display snapshot time**: Show when the data was generated

### Why This Matters
With 5-minute refresh:
- A "10:47" display will be wrong for 4 out of 5 minutes
- Users will assume the device is broken when time "jumps"
- Bucket notation (10:45–10:50) sets accurate expectations

## Data Contract

### Required Top-Level Fields
```json
{
  "meta": {
    "today_label": "Tue 16 Jan",
    "now_window": "10:45–10:50",
    "snapshot_local": "10:45",
    "next_refresh_local": "10:50",
    "refresh_minutes": 5
  },
  "day": {
    "start_local": "09:00",
    "end_local": "17:00",
    "current_slice": 22,
    "total_slices": 96,
    "progress_pct": 22.9
  },
  "now": {
    "event_type": "DW|M|SW|H|R|BU|PR|BG",
    "block_title": "Product Strategy",
    "subtitle": "One Thing: Finalize MVP Spec",
    "starts_local": "09:00",
    "ends_local": "10:30",
    "remaining_range_label": "40–45m",
    "bucket_hint": "Coarse time explanation",
    "block_dots": ["filled", "current", "empty", ...]
  },
  "next": { /* next block */ },
  "metrics": {
    "deepwork": { "tracked_min": 75, "planned_min": 210, ... },
    "tasks": { "done": 3, "total": 6, ... }
  },
  "pipeline": {
    "items": [{ "title", "status", "tags", "est_min", "emphasis" }]
  },
  "microsteps": {
    "items": [{ "title", "status": "done|doing|next" }]
  }
}
```

### Backend Responsibilities
- **Quantize all times** to 5-minute boundaries
- **Compute block_dots** array (one dot per 5-minute slice)
- **Calculate remaining time ranges** (min/max within bucket)
- **Merge calendar + tasks + tracking** before rendering

### Template Responsibilities
- **Keep it dumb**: No time math in Liquid
- **Just render**: Display pre-computed values
- **Use Framework v2 components**: `progress-bar`, `progress-dots`, `item`, `grid`

## Styling Rules
- **Colors**: Use only #000000 (Black) or #FFFFFF (White)
- **Framework v2 classes**: Use built-in utility classes
  - Layout: `.layout`, `.grid`, `.flex`
  - Components: `.title_bar`, `.item`, `.progress-bar`, `.progress-dots`
  - Emphasis: `.item--emphasis-1`, `.item--emphasis-2`, `.item--emphasis-3`
  - Sizes: `.title--large`, `.label--small`, `.value--xxsmall`
- **Overflow handling**: Use `data-overflow="true"` for lists
- **Text clamping**: Use `data-clamp="1"` to prevent layout breaks
- **Hierarchy**: Use font size, borders, and emphasis levels (not color shades)

## UI Anti-Patterns (FORBIDDEN)

❌ **NO button affordances** (no touch/tap UI elements)
❌ **NO smooth progress bars** (use segmented dots instead)
❌ **NO precise time claims** ("47m left" when refresh is every 5m)
❌ **NO live clocks** (will appear broken/frozen)
❌ **NO color gradients** (1-bit display)
❌ **NO animations** (e-ink doesn't support them)

## Views

### View 1: Command View (Default)
**Purpose**: Immediate orientation + execution
- Left pane (3/5): NOW block, NEXT block, day anchor
- Right pane (2/5): Metrics, pipeline, micro-steps
- All time expressed as buckets and ranges
- Progress shown as discrete 5-minute dots

### View 2: Ledger View (Optional)
**Purpose**: Plan vs actual audit
- Timeline of today's blocks
- Delta calculations (DW tracked vs planned)
- Task completion velocity

## Workflow
1. **Edit** `src/full.liquid` (use Framework v2 components)
2. **Update** `src/data.json` (must match schema)
3. **Preview** at `http://localhost:4567`
4. **Toggle** "E-ink" mode to verify B&W legibility
5. **Test** with different bucket times to verify no "live clock" illusions

## Integration Points
- **Calendar**: Google Calendar via TRMNL plugin or FateForger MCP
- **Tasks**: TickTick + Notion (merged feed)
- **Tracking**: Toggl (actual time spent)
- **Backend**: Python endpoint that generates JSON payload every 5 minutes

## Success Criteria
✅ No element suggests continuous/live time
✅ All remaining time shown as ranges or slices
✅ Snapshot time visible on every view
✅ NOW + NEXT + day position clear
✅ Plan vs reality metrics visible
✅ 5-minute refresh reflects correct bucket changes

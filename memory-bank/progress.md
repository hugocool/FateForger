# Progress (Updated: 2025-08-13)

## Done

- Renamed CalendarEvent.anchor_to_previous to anchor_prev with a short alias ap, clarified description, kept DB column name flex_back for backward compatibility, default True

## Doing



## Next

- Wire field usage where anchoring logic is applied in scheduling passes, if needed
- Add tests to cover ap=True/False semantics in duration-only events

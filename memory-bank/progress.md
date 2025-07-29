# Progress (Updated: 2025-07-29)

## Done

- Converted CalendarEvent and ScheduleDraft from JSON-based to relational one-to-many model
- Added foreign key schedule_draft_id to CalendarEvent
- Added back_populates relationships between models
- Identified SQLAlchemy 2.0 Mapped annotation requirements

## Doing

- Testing relational model in notebook
- Debugging SQLAlchemy relationship configuration
- Working around notebook metadata caching issues

## Next

- Get relational model working completely
- Create migration for production database
- Test full CRUD operations with relationships

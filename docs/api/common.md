# Common Utilities API

This module provides shared utilities, configuration management, and core services for the Admonish productivity bot.

## Configuration

::: productivity_bot.common.Config
    options:
      show_source: true
      show_signature_annotations: true

## Logging

::: productivity_bot.common.setup_logging
    options:
      show_source: true

::: productivity_bot.common.get_logger
    options:
      show_source: true

## MCP Integration

::: productivity_bot.common.mcp_query
    options:
      show_source: true

## BaseEventService

::: productivity_bot.common.BaseEventService
    options:
      show_source: true
      members:
        - __init__
        - list_events
        - get_event
        - create_event
        - update_event

## Planning Event Management

::: productivity_bot.common.find_planning_event
    options:
      show_source: true

::: productivity_bot.common.ensure_planning_event
    options:
      show_source: true

::: productivity_bot.common.create_planning_event
    options:
      show_source: true

## Event Dispatcher

::: productivity_bot.common.dispatch_event_change
    options:
      show_source: true

## Calendar Sync

::: productivity_bot.common.list_events_since
    options:
      show_source: true

::: productivity_bot.common.watch_calendar
    options:
      show_source: true

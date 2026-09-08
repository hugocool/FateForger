---
title: Architecture Overview
---

FateForger consists of cooperative agents. PlannerAgent creates sessions,
Haunter variants enforce completion, and RouterAgent dispatches Slack events.
APScheduler and SQLAlchemy provide scheduling and persistence.

A timeboxed block can also point at a piece of work — a Notion ticket kept
by handle in tmbx's own material store, resolved host-side before a
planning turn ever sees it. See
[Materials and Work Links](materials-and-work-links.md).

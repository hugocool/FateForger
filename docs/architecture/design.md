# System Design

## Overview

The productivity bot system is designed around a modular architecture with specialized components for different aspects of productivity management:

- **Calendar Integration**: Real-time synchronization with Google Calendar
- **AI Planning**: Intelligent task scheduling and optimization
- **Persistent Reminders**: Haunter bot for gentle but persistent engagement
- **Database Layer**: SQLAlchemy models with proper relationships

## Core Components

### Calendar Watch Server
Provides real-time calendar event notifications using Google Calendar API webhooks.

### Planner Bot
AI-powered planning agent that optimizes schedules and suggests task prioritization.

### Haunter Bot
Persistent notification system that ensures important tasks don't get forgotten.

### Scheduler
Background task execution and cron-like scheduling for automated operations.

## Data Flow

1. **Event Ingestion**: Calendar events are received via webhooks
2. **Processing**: Events are processed and stored in the database
3. **Planning**: AI planner analyzes tasks and suggests optimizations
4. **Execution**: Scheduler executes planned tasks at appropriate times
5. **Monitoring**: Haunter bot ensures follow-through on commitments

## Technology Stack

- **Backend**: Python with FastAPI/SQLAlchemy
- **Database**: SQLite with Alembic migrations
- **AI**: OpenAI API integration
- **Calendar**: Google Calendar API
- **Scheduling**: APScheduler for background tasks
- **Testing**: pytest with comprehensive coverage

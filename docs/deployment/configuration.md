# Configuration

## Environment Configuration

The application uses environment variables for configuration:

### Required Settings

```bash
# API Keys
GOOGLE_CALENDAR_API_KEY=your_google_api_key
OPENAI_API_KEY=your_openai_api_key

# Database
DATABASE_URL=sqlite:///data/admonish.db

# Logging
LOG_LEVEL=INFO
```

### Optional Settings

```bash
# Calendar Configuration
CALENDAR_SYNC_INTERVAL=300  # seconds
CALENDAR_WEBHOOK_URL=https://your-domain.com/webhook

# Bot Configuration
PLANNER_BOT_ENABLED=true
HAUNTER_BOT_ENABLED=true
NOTIFICATION_INTERVAL=3600  # seconds

# Performance
MAX_WORKERS=4
REQUEST_TIMEOUT=30
```

## Configuration Files

### Google Calendar API

1. Create a project in Google Cloud Console
2. Enable the Calendar API
3. Create service account credentials
4. Download the JSON key file
5. Set `GOOGLE_APPLICATION_CREDENTIALS` environment variable

### OpenAI API

1. Sign up for OpenAI API access
2. Generate an API key
3. Set `OPENAI_API_KEY` environment variable

## Database Configuration

### SQLite (Development)

```bash
DATABASE_URL=sqlite:///data/admonish.db
```

### PostgreSQL (Production)

```bash
DATABASE_URL=postgresql://user:password@host:port/database
```

## Logging Configuration

Logs are structured JSON format with configurable levels:

* `DEBUG` - Detailed debugging information
* `INFO` - General operational messages
* `WARNING` - Warning conditions
* `ERROR` - Error conditions
* `CRITICAL` - Critical error conditions

## Security Considerations

* Store API keys securely (use secrets management)
* Enable HTTPS in production
* Validate all input data
* Use database connection pooling
* Implement rate limiting for API endpoints

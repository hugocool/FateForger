# Docker Deployment

## Docker Configuration

The project includes Docker configuration for easy deployment:

### Development Environment

```bash
# Build and run development containers
docker-compose up -d

# View logs
docker-compose logs -f

# Stop containers
docker-compose down
```

### Production Deployment

```bash
# Build production image
docker build -t productivity-bot .

# Run with environment variables
docker run -d \
  -e GOOGLE_CALENDAR_API_KEY=your_key \
  -e OPENAI_API_KEY=your_key \
  -e DATABASE_URL=your_db_url \
  productivity-bot
```

## Container Structure

* **Base Image**: Python 3.10 slim
* **Dependencies**: Managed via Poetry
* **Volumes**: Persistent data storage
* **Networking**: Exposed ports for API access

## Environment Variables

Required environment variables:

* `GOOGLE_CALENDAR_API_KEY` - Google Calendar API credentials
* `OPENAI_API_KEY` - OpenAI API key
* `DATABASE_URL` - Database connection string
* `LOG_LEVEL` - Logging level (default: INFO)

## Health Checks

The container includes health check endpoints:

* `/health` - Basic health status
* `/ready` - Readiness probe for Kubernetes
* `/metrics` - Prometheus metrics (if enabled)

## Scaling Considerations

* Stateless design allows horizontal scaling
* Database connections should be pooled
* Background tasks can be distributed
* Use load balancers for high availability

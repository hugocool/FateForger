# TickTick MCP Server Integration - Complete Setup Guide

This document provides a comprehensive guide for setting up the TickTick MCP server integration with your Admonish bot, including all troubleshooting steps and timeout protection.

## 🎯 Overview

We've successfully created a complete TickTick MCP (Model Context Protocol) server integration that:

- ✅ Uses a custom Dockerfile with timeout protection to prevent hanging
- ✅ Integrates with AutoGen agents following your architectural requirements
- ✅ Provides comprehensive task management capabilities
- ✅ Includes proper error handling and connection timeouts
- ✅ Follows the same pattern as your Google Calendar MCP server

## 🏗️ Architecture

```
📱 Jupyter Notebook
↕️  AutoGen Assistant Agent (with timeout protection)
↕️  MCP Protocol (HTTP with connection timeouts) 
↕️  TickTick MCP Server (Docker with startup timeouts)
↕️  TickTick API (OAuth with token persistence)
📋 TickTick Tasks & Projects
```

## 📁 Files Created/Modified

### 1. Custom Dockerfile (`infra/Dockerfile.ticktick-mcp`)
- **Purpose**: Packages the TickTick MCP server for Docker deployment
- **Key Features**:
  - Timeout protection for git clone and pip install operations
  - Startup script with 30-second timeout to prevent hanging
  - Health check script with 5-second timeout
  - Proper error messaging for credential issues
  - Volume mount for OAuth token persistence

### 2. Docker Compose Updates (`infra/docker-compose.yml`)
- **Added**: TickTick MCP service definition
- **Updated**: Bot service dependencies to include TickTick MCP
- **Added**: `TICKTICK_MCP_ENDPOINT` environment variable
- **Added**: `ticktick-mcp-config` volume for token persistence

### 3. Environment Configuration (`.env`)
- **Added**: All TickTick MCP configuration variables
- **Variables**: 
  - `TICKTICK_MCP_VERSION=main`
  - `TICKTICK_SERVER_*` settings
  - `TICKTICK_USERNAME`, `TICKTICK_PASSWORD` 
  - `TICKTICK_CLIENT_ID`, `TICKTICK_CLIENT_SECRET`
  - `TICKTICK_REDIRECT_URI`

### 4. Notebook Updates (`notebooks/ticktick_agent.ipynb`)
- **Added**: Timeout-protected MCP client functions
- **Added**: Connection timeout settings (10s for connection, 30s for responses)
- **Added**: Comprehensive error handling for hanging prevention
- **Added**: Connectivity test functions with timeout protection

### 5. Documentation (`docs/setup/ticktick-mcp-setup.md`)
- **Contains**: Complete setup instructions
- **Includes**: TickTick API registration process
- **Covers**: OAuth authentication flow
- **Provides**: Troubleshooting guide

## 🛡️ Timeout Protection Measures

### Docker Level
```dockerfile
# Git clone with 60s timeout
RUN timeout 60 git clone https://github.com/jen6/ticktick-mcp.git .

# Pip install with 300s timeout  
RUN timeout 300 pip install --no-cache-dir -e .

# Server startup with 30s timeout
exec timeout 30 python -m ticktick_mcp
```

### Health Check
```dockerfile
# Aggressive health check timeouts
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=2
```

### Python Client Level
```python
# Connection timeout: 10 seconds
CONNECTION_TIMEOUT = 10.0

# Question timeout: 30 seconds  
QUESTION_TIMEOUT = 30.0

# Async timeout protection
response = await asyncio.wait_for(
    agent.on_messages([message], CancellationToken()),
    timeout=QUESTION_TIMEOUT
)
```

## 🔧 Available TickTick Tools

Once the server is running and authenticated, you'll have access to:

### Task Management
1. `ticktick_create_task` - Create new tasks with due dates, priorities
2. `ticktick_update_task` - Update existing tasks  
3. `ticktick_delete_tasks` - Delete single or multiple tasks
4. `ticktick_complete_task` - Mark tasks as complete
5. `ticktick_move_task` - Move tasks between projects
6. `ticktick_make_subtask` - Create parent-child task relationships

### Task Retrieval
7. `ticktick_get_by_id` - Get specific tasks/projects by ID
8. `ticktick_get_all` - Get all tasks, projects, or tags
9. `ticktick_get_tasks_from_project` - Get tasks from specific project
10. `ticktick_filter_tasks` - Advanced filtering by criteria

### Utilities
11. `ticktick_convert_datetime_to_ticktick_format` - Date format conversion

## 🚀 Setup Process

### Step 1: Register TickTick Application
1. Go to [TickTick OpenAPI Documentation](https://developer.ticktick.com/docs#/openapi)
2. Log in with your TickTick account
3. Click "Manage Apps" → "+App Name"  
4. Note your `Client ID` and `Client Secret`
5. Set `OAuth Redirect URL` to: `http://127.0.0.1:8080`

### Step 2: Configure Environment
Update your `.env` file with real credentials:

```bash
TICKTICK_USERNAME=your_email@example.com
TICKTICK_PASSWORD=your_password
TICKTICK_CLIENT_ID=your_actual_client_id
TICKTICK_CLIENT_SECRET=your_actual_client_secret
TICKTICK_REDIRECT_URI=http://127.0.0.1:8080
```

### Step 3: Build and Start Services
```bash
cd infra

# Build the TickTick MCP image
docker-compose build ticktick-mcp

# Start the service
docker-compose up -d ticktick-mcp

# Check logs for OAuth authentication
docker-compose logs -f ticktick-mcp
```

### Step 4: Complete OAuth Authentication
1. Check logs for authentication URL
2. Open URL in browser and authorize
3. Copy the redirected URL (with code parameter)
4. Paste into terminal when prompted
5. Token will be persisted in Docker volume

### Step 5: Test Integration
Open `notebooks/ticktick_agent.ipynb` and run the connectivity test cells.

## 🔍 Troubleshooting

### Hanging Issues
- **Server startup hangs**: Check Docker logs, verify credentials
- **Client connection hangs**: All connections have 10s timeout
- **Agent responses hang**: All questions have 30s timeout

### Common Problems

#### "Connection timed out after 10s"
- Server may be starting up (check logs)
- Credentials may be incorrect
- OAuth authentication may be needed

#### "Server startup timed out after 30s"  
- Usually means authentication is needed
- Check if credentials are placeholder values
- Verify TickTick API credentials are correct

#### "MCP tools connection failed"
- Ensure server is running: `docker-compose ps`
- Check network connectivity: `docker-compose logs ticktick-mcp`
- Verify port 8150 is not blocked

### Commands for Debugging
```bash
# Check service status
docker-compose ps

# View server logs
docker-compose logs ticktick-mcp

# Restart server
docker-compose restart ticktick-mcp

# Stop server
docker-compose stop ticktick-mcp

# Remove and rebuild
docker-compose down ticktick-mcp
docker-compose build ticktick-mcp
docker-compose up -d ticktick-mcp
```

## 💡 Key Design Decisions

### Why Custom Dockerfile?
- The upstream TickTick MCP repository doesn't provide a Docker image
- We needed timeout protection to prevent hanging
- Required specific environment setup for OAuth token persistence

### Why Timeout Protection?
- Prevents infinite hanging during Docker build
- Protects against unresponsive server startup
- Guards client connections against blocking indefinitely
- Follows your explicit "NO HANGING" requirement

### Why AutoGen MCP Integration?
- Maintains your architectural choice to use AutoGen AssistantAgent
- Uses AutoGen's built-in MCP system (not manual HTTP calls)
- Preserves the same pattern as Google Calendar MCP integration

## 🎉 Success Indicators

When setup is complete, you'll see:

1. **Docker**: Service is healthy in `docker-compose ps`
2. **Logs**: No authentication errors in server logs
3. **Notebook**: Connectivity test passes with tool count > 0
4. **Agent**: Can create and retrieve tasks through natural language

## 📋 Next Steps

1. **Test Basic Operations**: Create, update, and retrieve tasks
2. **Integrate with Calendar**: Combine TickTick and Calendar data
3. **Automate Workflows**: Set up scheduled task creation
4. **Monitor Performance**: Watch for timeout issues in production

## 🔗 Related Documentation

- [TickTick MCP Setup Guide](./ticktick-mcp-setup.md)
- [Docker Compose Configuration](../infra/docker-compose.yml)
- [Test Notebook](../notebooks/ticktick_agent.ipynb)
- [Dockerfile](../infra/Dockerfile.ticktick-mcp)

---

*This integration provides robust, timeout-protected access to TickTick functionality while maintaining your project's architectural requirements and preventing hanging issues.*

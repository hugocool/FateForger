# TickTick MCP Server Setup Guide

This guide will help you set up the TickTick MCP server for integration with your Admonish bot.

## Prerequisites

- TickTick account
- Access to TickTick Developer API

## Step 1: Register TickTick Application

1. Go to the [TickTick OpenAPI Documentation](https://developer.ticktick.com/docs#/openapi)
2. Log in with your TickTick account
3. Click on `Manage Apps` in the top right corner
4. Register a new app by clicking the `+App Name` button
5. Provide a name for your application (e.g., "Admonish MCP Server")
6. Once created, edit the app details and note down:
   - **Client ID**
   - **Client Secret**
7. Set the **OAuth Redirect URL** to: `http://127.0.0.1:8080`
   - This must match exactly what you put in your `.env` file

## Step 2: Configure Environment Variables

Update your `.env` file with your TickTick credentials:

```bash
# TickTick MCP Configuration
TICKTICK_MCP_VERSION=main
TICKTICK_SERVER_TRANSPORT=stdio
TICKTICK_SERVER_HOST=0.0.0.0
TICKTICK_SERVER_PORT=8150
TICKTICK_USERNAME=your_ticktick_email@example.com
TICKTICK_PASSWORD=your_ticktick_password
TICKTICK_CLIENT_ID=your_client_id_from_step_1
TICKTICK_CLIENT_SECRET=your_client_secret_from_step_1
TICKTICK_REDIRECT_URI=http://127.0.0.1:8080
```

Replace the placeholder values with your actual TickTick credentials:
- `TICKTICK_USERNAME`: Your TickTick login email
- `TICKTICK_PASSWORD`: Your TickTick password (or app password if 2FA is enabled)
- `TICKTICK_CLIENT_ID`: Client ID from Step 1
- `TICKTICK_CLIENT_SECRET`: Client Secret from Step 1

## Step 3: Initial OAuth Authentication

The first time you run the TickTick MCP server, it will need to perform OAuth authentication:

1. Start the services: `docker-compose up -d ticktick-mcp`
2. Check the logs: `docker-compose logs ticktick-mcp`
3. If OAuth authentication is needed, you'll see a URL in the logs
4. Open the URL in your browser and authorize the application
5. You'll be redirected to your `TICKTICK_REDIRECT_URI`
6. Copy the full redirected URL (including the `code=` parameter)
7. Paste it into the terminal when prompted

After successful authentication, a `.token-oauth` file will be created and persisted in the Docker volume.

## Step 4: Test the Setup

1. Start all services: `docker-compose up -d`
2. Check that all services are healthy: `docker-compose ps`
3. Test the TickTick MCP connection from your bot

## Available TickTick MCP Tools

Once set up, you'll have access to these TickTick tools:

### Task Management
- `ticktick_create_task` - Create new tasks
- `ticktick_update_task` - Update existing tasks
- `ticktick_delete_tasks` - Delete tasks
- `ticktick_complete_task` - Mark tasks as complete
- `ticktick_move_task` - Move tasks between projects
- `ticktick_make_subtask` - Create subtasks

### Task Retrieval
- `ticktick_get_by_id` - Get specific task by ID
- `ticktick_get_all` - Get all tasks, projects, or tags
- `ticktick_get_tasks_from_project` - Get tasks from specific project
- `ticktick_filter_tasks` - Filter tasks by various criteria

### Helper Tools
- `ticktick_convert_datetime_to_ticktick_format` - Convert datetime formats

## Troubleshooting

### Authentication Issues
- Ensure your `TICKTICK_REDIRECT_URI` matches exactly what you set in the TickTick app
- Check that your credentials are correct in the `.env` file
- Look at the container logs: `docker-compose logs ticktick-mcp`

### Connection Issues
- Verify the TickTick MCP service is healthy: `docker-compose ps`
- Check network connectivity between bot and TickTick MCP container
- Ensure the `TICKTICK_MCP_ENDPOINT` environment variable is set correctly in the bot service

### Token Expiration
- OAuth tokens typically last ~6 months
- If authentication fails, delete the volume and re-authenticate:
  ```bash
  docker-compose down
  docker volume rm infra_ticktick-mcp-config
  docker-compose up -d ticktick-mcp
  ```

## Integration with AutoGen

The TickTick MCP server can be integrated with your AutoGen agents following the same pattern as the Google Calendar MCP:

```python
# Configure AutoGen agent with TickTick MCP tools
ticktick_agent = AssistantAgent(
    name="ticktick_assistant",
    llm_config={
        "model": "gpt-4",
        "api_key": config.openai_api_key,
    },
    system_message="You are a TickTick task management assistant...",
)

# Use TickTick MCP endpoint
mcp_client = MCPClient("http://ticktick-mcp:8150")
```

## Security Notes

- Never commit your `.env` file with real credentials
- Use app passwords if you have 2FA enabled on TickTick
- The OAuth token is stored securely in a Docker volume
- Consider using Docker secrets for production deployments

# AutoGen Integration — Review System

Three usage modes. Pick the one that fits your context.

---

## Mode 1: McpWorkbench + Docker (recommended for production)

The agent discovers tools, reads the guidelines resource, and gets the reasoning
template — all at runtime. No system prompt needed.

```bash
# Start the server
docker compose up -d
```

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.anthropic import AnthropicChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, SseServerParams

async def run():
    params = SseServerParams(url="http://localhost:8000/sse")

    async with McpWorkbench(params) as wb:
        # Agent discovers all 8 tools automatically.
        # Tool descriptions hint at mcp://review/guidelines —
        # agent reads it before acting (Capability-Based Discovery).
        tools = wb.as_tools()

        # Get the reasoning template from the server's prompt endpoint.
        # This bootstraps the agent without needing SKILL.md in the repo.
        prompt = await wb.get_prompt("review_session")

        agent = AssistantAgent(
            name="ReviewAgent",
            model_client=AnthropicChatCompletionClient(model="claude-sonnet-4-20250514"),
            tools=tools,
            system_message=prompt,  # Server-side prompt, always up to date
        )

        await Console(agent.run_stream(task="Start my weekly review."))

asyncio.run(run())
```

**Why this mode:** The agent gets everything from the server — tools, guidelines,
prompt template. You can update the server without touching agent code.
The McpWorkbench pattern matches the Broker Pattern from the Agent Catalogue:
capability is self-documenting and discoverable at runtime.

---

## Mode 2: StdioMCP + local process (dev / no Docker)

```python
import asyncio
import os
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.anthropic import AnthropicChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams
from dotenv import load_dotenv

load_dotenv()

async def run():
    params = StdioServerParams(
        command="python",
        args=["-m", "mcp.server"],
        env={
            "NOTION_TOKEN": os.getenv("NOTION_TOKEN"),
            "WEEKLY_REVIEWS_DB_ID": os.getenv("WEEKLY_REVIEWS_DB_ID"),
            "OUTCOMES_DB_ID": os.getenv("OUTCOMES_DB_ID"),
        }
    )

    async with McpWorkbench(params) as wb:
        tools = wb.as_tools()
        prompt = await wb.get_prompt("review_session")

        agent = AssistantAgent(
            name="ReviewAgent",
            model_client=AnthropicChatCompletionClient(model="claude-sonnet-4-20250514"),
            tools=tools,
            system_message=prompt,
        )

        await Console(agent.run_stream(task="Start my weekly review."))

asyncio.run(run())
```

---

## Mode 3: Direct import (no MCP overhead, tightest integration)

When you want to embed the tool layer directly in an AutoGen agent
without running a separate server. Useful for testing or lightweight setups.

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.anthropic import AnthropicChatCompletionClient
from autogen_core.tools import FunctionTool
from pathlib import Path

# Direct imports — no MCP server needed
from tools.read import get_last_review, get_reviews, get_outcomes
from tools.write import (
    create_review, patch_review_field,
    append_phase_content, create_outcome, update_outcome_status
)

# Wrap as AutoGen FunctionTools
tools = [FunctionTool(fn, description=fn.__doc__) for fn in [
    get_last_review, get_reviews, get_outcomes,
    create_review, patch_review_field, append_phase_content,
    create_outcome, update_outcome_status,
]]

# Load SKILL.md as system prompt (same content as mcp://review/guidelines)
skill = (Path(__file__).parent / "skills" / "review_system" / "SKILL.md").read_text()

async def run():
    agent = AssistantAgent(
        name="ReviewAgent",
        model_client=AnthropicChatCompletionClient(model="claude-sonnet-4-20250514"),
        tools=tools,
        system_message=skill,
    )
    await Console(agent.run_stream(task="Start my weekly review."))

asyncio.run(run())
```

---

## Multi-agent: review → timebox → scrum handoff

All three agents share the same MCP server (or direct imports).
Review agent writes directives to Notion at Phase 5.
Timebox and scrum agents read them as their session starts.

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_ext.models.anthropic import AnthropicChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, SseServerParams
from pathlib import Path

def load_skill(name: str) -> str:
    return (Path(__file__).parent / "skills" / name / "SKILL.md").read_text()

async def run():
    model = AnthropicChatCompletionClient(model="claude-sonnet-4-20250514")
    params = SseServerParams(url="http://localhost:8000/sse")

    async with McpWorkbench(params) as wb:
        tools = wb.as_tools()

        # Each agent gets the same tools but a different skill prompt
        review_agent = AssistantAgent(
            name="ReviewAgent",
            model_client=model,
            tools=tools,
            system_message=await wb.get_prompt("review_session"),
        )

        timebox_agent = AssistantAgent(
            name="TimeboxAgent",
            model_client=model,
            tools=tools,
            system_message=load_skill("timebox"),
        )

        scrum_agent = AssistantAgent(
            name="ScrumAgent",
            model_client=model,
            tools=tools,
            system_message=load_skill("scrum"),
        )

        team = RoundRobinGroupChat(
            participants=[review_agent, timebox_agent, scrum_agent],
            termination_condition=TextMentionTermination("HANDOFF_COMPLETE"),
        )

        await team.run(task="""
            ReviewAgent: run the full weekly review. When Phase 5 is complete
            and all fields are written to Notion, say REVIEW_COMPLETE.

            TimeboxAgent: after REVIEW_COMPLETE, call get_last_review() and
            read timebox_directives. State the constraints you will apply
            this week, then say TIMEBOX_CONFIRMED.

            ScrumAgent: after TIMEBOX_CONFIRMED, call get_last_review() and
            read scrum_directives. State which tickets you will sharpen and
            what DoD patterns apply, then say HANDOFF_COMPLETE.
        """)

asyncio.run(run())
```

---

## Claude Desktop (no code)

Add to `claude_desktop_config.json` under `mcpServers`:

```json
"review-system": {
  "command": "python",
  "args": ["-m", "mcp.server"],
  "env": {
    "NOTION_TOKEN": "ntn_...",
    "WEEKLY_REVIEWS_DB_ID": "...",
    "OUTCOMES_DB_ID": "..."
  }
}
```

Claude Desktop discovers all tools + the guidelines resource + prompt templates
automatically. Start a session with: `"Start my weekly review."`

---

## How the Context Triad works at runtime

```
Agent starts
    │
    ├─ McpWorkbench.list_tools()
    │   └─ sees: get_last_review, create_review, patch_review_field...
    │      each tool description hints: "read mcp://review/guidelines first"
    │
    ├─ McpWorkbench.get_prompt("review_session")
    │   └─ returns: full reasoning chain with phase structure + rules
    │      agent uses this as system_message
    │
    └─ Agent first message → reads mcp://review/guidelines (resource)
        └─ now has: complete SKILL.md content in context
           knows: all gate conditions, extraction rules, write triggers
           acts: Socratically, incrementally, with correct tool calls
```

The key: the agent doesn't need the SKILL.md pre-loaded in the codebase.
It discovers and reads it at runtime via the resource endpoint.
Update the server → all agents get the update. No redeploys needed.

---

## Requirements

```
# requirements.txt
mcp[cli]>=1.0.0
fastmcp>=0.1.0
ultimate-notion>=0.8.0
autogen-agentchat>=0.4.0
autogen-ext[mcp,anthropic]>=0.4.0
python-dotenv>=1.0.0
```

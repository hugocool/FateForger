---
title: FateForger
---

# FATEFORGER

*Being productive is no longer optional.* 

FateForger is an **agentic productivity framework** that proactively plans your day, haunts incomplete tasks, and syncs across Google Calendar and Slack. Powered by cooperative agents orchestrated with AutoGen and backed by MCP servers.

---

## THE AGENTS

<div class="ff-agents-grid">
  <div class="ff-agent-card">
    <img class="ff-agent-avatar" src="agent_icons/TaskMarshal.png" alt="TaskMarshal">
    <p class="ff-agent-name">TASKMARSHAL</p>
    <p class="ff-agent-role">Task orchestration & routing</p>
  </div>
  <div class="ff-agent-card">
    <img class="ff-agent-avatar" src="agent_icons/Schedular.png" alt="Schedular">
    <p class="ff-agent-name">SCHEDULAR</p>
    <p class="ff-agent-role">Intelligent calendar planning</p>
  </div>
  <div class="ff-agent-card">
    <img class="ff-agent-avatar" src="agent_icons/Admonisher.png" alt="Admonisher">
    <p class="ff-agent-name">ADMONISHER</p>
    <p class="ff-agent-role">Persistent task haunting</p>
  </div>
  <div class="ff-agent-card">
    <img class="ff-agent-avatar" src="agent_icons/Revisor.png" alt="Revisor">
    <p class="ff-agent-name">REVISOR</p>
    <p class="ff-agent-role">Retrospective analysis</p>
  </div>
</div>

---

## QUICK START

```bash
# Clone and install
poetry install
cp .env.template .env

# Initialize database and start services
poetry run python scripts/init_db.py
./run.sh
```

For complete installation details see [Setup](setup/installation.md).

---

## CORE CAPABILITIES

- **Proactive Planning**: Agents analyze your calendar and automatically schedule tasks
- **Task Haunting**: Persistent reminders ensure nothing falls through the cracks
- **Multi-Platform Sync**: Seamless integration with Google Calendar, Slack, Notion, and TickTick
- **MCP Architecture**: Built on the Model Context Protocol for extensibility and power

---
title: FateForger
---

# FATEFORGER

*Being productive is no longer optional.* 

FateForger is an **agentic productivity framework** that proactively plans your day, haunts incomplete tasks, and syncs across Google Calendar and Slack. Powered by cooperative agents orchestrated with AutoGen and backed by MCP servers.

---

## THE AGENTS

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 30px; margin: 40px 0; text-align: center;">
  <div style="background: linear-gradient(135deg, rgba(0,168,198,0.05), rgba(0,78,100,0.05)); padding: 20px; border-radius: 8px; border: 1px solid rgba(0,168,198,0.2);">
    <img src="agent_icons/TaskMarshal.png" alt="TaskMarshal" style="width: 120px; height: 120px; margin: 10px auto; filter: drop-shadow(0 0 15px rgba(0,168,198,0.4));">
    <p style="font-family: 'Syne', sans-serif; font-weight: 800; font-size: 1.2em; color: #D4AF37; margin-top: 15px;">TASKMARSHAL</p>
    <p style="font-size: 0.9em; color: #00A8C6; margin-top: 5px;">Task orchestration & routing</p>
  </div>
  <div style="background: linear-gradient(135deg, rgba(0,168,198,0.05), rgba(0,78,100,0.05)); padding: 20px; border-radius: 8px; border: 1px solid rgba(0,168,198,0.2);">
    <img src="agent_icons/Schedular.png" alt="Schedular" style="width: 120px; height: 120px; margin: 10px auto; filter: drop-shadow(0 0 15px rgba(0,168,198,0.4));">
    <p style="font-family: 'Syne', sans-serif; font-weight: 800; font-size: 1.2em; color: #D4AF37; margin-top: 15px;">SCHEDULAR</p>
    <p style="font-size: 0.9em; color: #00A8C6; margin-top: 5px;">Intelligent calendar planning</p>
  </div>
  <div style="background: linear-gradient(135deg, rgba(0,168,198,0.05), rgba(0,78,100,0.05)); padding: 20px; border-radius: 8px; border: 1px solid rgba(0,168,198,0.2);">
    <img src="agent_icons/Admonisher.png" alt="Admonisher" style="width: 120px; height: 120px; margin: 10px auto; filter: drop-shadow(0 0 15px rgba(0,168,198,0.4));">
    <p style="font-family: 'Syne', sans-serif; font-weight: 800; font-size: 1.2em; color: #D4AF37; margin-top: 15px;">ADMONISHER</p>
    <p style="font-size: 0.9em; color: #00A8C6; margin-top: 5px;">Persistent task haunting</p>
  </div>
  <div style="background: linear-gradient(135deg, rgba(0,168,198,0.05), rgba(0,78,100,0.05)); padding: 20px; border-radius: 8px; border: 1px solid rgba(0,168,198,0.2);">
    <img src="agent_icons/Revisor.png" alt="Revisor" style="width: 120px; height: 120px; margin: 10px auto; filter: drop-shadow(0 0 15px rgba(0,168,198,0.4));">
    <p style="font-family: 'Syne', sans-serif; font-weight: 800; font-size: 1.2em; color: #D4AF37; margin-top: 15px;">REVISOR</p>
    <p style="font-size: 0.9em; color: #00A8C6; margin-top: 5px;">Retrospective analysis</p>
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

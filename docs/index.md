# FateForger - Agentic AI Productivity System

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Poetry](https://img.shields.io/badge/dependency%20management-poetry-blue.svg)](https://python-poetry.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An intelligent productivity system that **forges your fate** through relentless accountability and intelligent planning. Unlike traditional productivity tools that passively wait for you to open them, FateForger actively shapes your destiny by ensuring you maintain your planning rituals and follow through on commitments.

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/hugocool/fateforger.git
cd fateforger

# Install dependencies with Poetry
poetry install

# Set up environment variables
cp .env.template .env
# Edit .env with your configuration

# Initialize the database
poetry run python scripts/init_db.py

# Run the application
poetry run python -m productivity_bot
```

## 🔧 System Architecture Overview

### ⚙️ **Agentic AI Productivity Framework**

At its core, this system is composed of **multiple agents**, each with a specific role in your daily productivity lifecycle. Together, they create a **self-maintaining operating system for your life**.

---

### 🧠 **1. Planning Agent** — *"The Ritual Enforcer"*

#### Role:

* Makes sure you **plan your day every day**, at a set ritual time (typically during your wind-down).
* If you don't, it **escalates**, because *"failing to plan is planning to fail."*

#### Goals:

* Check your calendar for a **"daily planning" event**.
* If it's missing: prompt you to create it (via chat).
* If it's present: trigger a planning conversation in Slack (or another chat interface).
* Ensures each planning session gets **its own thread or context**, to keep memory and accountability scoped.

#### Context Captured:

* Today's calendar.
* Yesterday's unfinished tasks (pulled from Notion, TickTick, or a planner file).
* Recent Slack conversations (if relevant).
* Your preferred timeboxing template (e.g., MITs, deep work blocks).

#### Interaction:

* Starts a thread when the time comes.
* Uses interactive messages (Slack modals, buttons) to prompt input.
* Offers reschedule/snooze **only if reason is strong enough**.
* Fails loudly if ignored.

---

### 👻 **2. Haunting Agent** — *"The Enforcer"*

#### Role:

* Keeps tabs on your **response to the planning agent**.
* If you start but don't finish the plan: follows up.
* If you ignore it entirely: nudges with increasing urgency (exponential backoff).
* Eventually offers **summary and reconciliation prompts** (e.g., "Hey, seems we didn't plan today. Want to log why?").

#### Interaction:

* Sends ephemeral messages or modals (to avoid noisy threads).
* Can even be split into a **separate Slack bot** to keep DMs clean (e.g., one bot for "planning," one for "haunting").

#### Intelligence:

* Aware of your current focus mode (based on calendar or Do Not Disturb).
* Bundles missed interactions to avoid spam.

---

### 📅 **3. Calendar Watcher (MCP Client)**

#### Role:

* Watches your Google Calendar for:

  * New planning events.
  * Modifications to planning sessions (start time moved, etc).
  * Events that should be reflected in Slack threads (like focus blocks).

#### Tech:

* Uses **push notifications (webhooks)** from Google Calendar.
* Stores relevant events in internal state (or database).
* Can optionally auto-tag or color-code planning sessions.

---

### 💬 **4. Slack Bot Interface**

#### Role:

* Central messaging interface between the user and agents.
* Sends initial planning prompts.
* Maintains context through **one thread per day**.
* Sends ephemeral follow-ups, interactive buttons/modals.

#### Unique Features:

* Planning sessions live in a daily thread, reducing clutter.
* Interactive Slack UI lets you postpone, mark incomplete tasks, or carry over actions.
* Can display calendar summaries or todo previews inline.

---

### 🧠 **LLM Agent Core (via Autogen)**

* Powers the "brains" of each agent.
* Responds to calendar events, Slack triggers, or missed interactions.
* Uses structured generation (e.g., JSON mode via Outlines) for logging plans, extracting tasks, and updating other systems.
* Makes decisions about whether to escalate, defer, or let go.

---

## 💡 Why This Is Unique (vs Competitors)

| Most Productivity Tools                                     | This Agentic System                                                        |
| ----------------------------------------------------------- | -------------------------------------------------------------------------- |
| Rely on you to open the app and do the work.                | Admonishes and haunts you until you *do the work*.                         |
| Let you skip your routines silently.                        | Escalates, questions, and logs when you miss a ritual.                     |
| Offer static dashboards or checklists.                      | Actively reshapes and synchronizes your day with what *you said* you'd do. |
| Require expert knowledge to set up (e.g. Notion templates). | Can be installed and taught to orchestrate your system itself.             |
| Have no memory or daily context.                            | Each day lives in a discrete conversation with persistent memory.          |

## 🛠️ Core Features

- **🤖 Multi-Agent Architecture**: Specialized agents for planning, haunting, and calendar management
- **📅 Calendar Integration**: Deep Google Calendar integration via MCP (Model Context Protocol)
- **💬 Slack Integration**: Rich interactive interface with threads and modals
- **🔄 Event-Driven Design**: Reactive system that responds to calendar changes and user interactions
- **📊 Persistent Scheduling**: APScheduler with database persistence for reliable job execution
- **🎯 Exponential Backoff**: Smart escalation system that increases urgency over time
- **🧠 LLM-Powered**: Uses AutoGen for intelligent agent decision-making
- **🗄️ Database-First**: SQLAlchemy models with Alembic migrations for data persistence

## 📁 Project Structure

```
admonish/
├── src/productivity_bot/    # Main application code
│   ├── models.py           # Database models
│   ├── common.py           # Shared utilities and services
│   ├── scheduler.py        # Job scheduling system
│   ├── planner_bot.py      # Planning agent implementation
│   ├── haunter_bot.py      # Haunting agent implementation
│   └── calendar_watch_server.py  # Calendar webhook handler
├── tests/                  # Test suite
├── docs/                   # Documentation
├── data/                   # Database files
├── scripts/                # Utility scripts
├── alembic/               # Database migrations
└── logs/                  # Application logs
```

## 🔧 Development

### Prerequisites

- Python 3.10+
- Poetry for dependency management
- Google Calendar API access
- Slack App credentials

### Environment Setup

1. **Install Dependencies**:
   ```bash
   poetry install
   ```

2. **Configure Environment**:
   ```bash
   cp .env.template .env
   # Edit .env with your API keys and configuration
   ```

3. **Initialize Database**:
   ```bash
   poetry run python scripts/init_db.py
   ```

4. **Run Tests**:
   ```bash
   poetry run pytest
   ```

5. **Start Development Server**:
   ```bash
   poetry run python -m productivity_bot
   ```

### Code Quality

This project uses several tools to maintain code quality:

- **Black**: Code formatting
- **isort**: Import sorting
- **flake8**: Linting
- **mypy**: Type checking
- **pytest**: Testing

Run all quality checks:
```bash
make lint format test
```

## 📚 Documentation

- [Architecture Overview](architecture/overview.md) - Deep dive into system design
- [API Reference](api/common.md) - Complete API documentation
- [Development Guide](development/setup.md) - Setup and contribution guidelines
- [Deployment](deployment/docker.md) - Production deployment instructions

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](development/contributing.md) for details.

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Built with [AutoGen](https://github.com/microsoft/autogen) for agent orchestration
- Uses [MCP](https://modelcontextprotocol.io/) for calendar integration
- Powered by [Slack Bolt](https://slack.dev/bolt-python/) for rich interactions

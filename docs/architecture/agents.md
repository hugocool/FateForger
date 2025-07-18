---
title: Agents
---

## PlannerAgent

- Triggered by Slack commands or cron
- Stores planning sessions in `models.py`

## BootstrapHaunter

- Watches for unsubmitted plans

## CommitmentHaunter

- Reminds about incomplete tasks

## RouterAgent

- Dispatches Slack events via `slack_router.py`

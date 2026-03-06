"""
One-time DB creation script.
Run this if the setup wizard fails to create the DBs via the browser.

Usage:
    NOTION_TOKEN=ntn_... python notion_schema/init_db.py --page-id PAGE_ID
"""
import os
import sys
import argparse
import json


def create_databases(token: str, page_id: str):
    import urllib.request
    import urllib.error

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    def notion_post(endpoint: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"https://api.notion.com/v1/{endpoint}",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    # Create Weekly Reviews DB
    print("Creating Weekly Reviews DB...")
    reviews_db = notion_post("databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": "Weekly Reviews"}}],
        "properties": {
            "week":                 {"date": {}},
            "intention":            {"rich_text": {}},
            "wip_count":            {"number": {"format": "number"}},
            "themes":               {"rich_text": {}},
            "failure_looks_like":   {"rich_text": {}},
            "thursday_signal":      {"rich_text": {}},
            "clarity_gaps":         {"rich_text": {}},
            "timebox_directives":   {"rich_text": {}},
            "scrum_directives":     {"rich_text": {}},
        }
    })
    reviews_id = reviews_db["id"]
    print(f"  → Weekly Reviews DB: {reviews_id}")

    # Create Outcomes DB
    print("Creating Outcomes DB...")
    outcomes_db = notion_post("databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": "Outcomes"}}],
        "properties": {
            "title": {"title": {}},
            "dod":   {"rich_text": {}},
            "priority": {
                "select": {"options": [
                    {"name": "Must",    "color": "green"},
                    {"name": "Support", "color": "blue"},
                ]}
            },
            "status": {
                "select": {"options": [
                    {"name": "Hit",     "color": "green"},
                    {"name": "Partial", "color": "yellow"},
                    {"name": "Miss",    "color": "red"},
                ]}
            },
            "ticket": {"url": {}},
            "review": {
                "relation": {
                    "database_id": reviews_id,
                    "single_property": {},
                }
            },
        }
    })
    outcomes_id = outcomes_db["id"]
    print(f"  → Outcomes DB: {outcomes_id}")

    print("\nAdd these to your .env:")
    print(f"WEEKLY_REVIEWS_DB_ID={reviews_id}")
    print(f"OUTCOMES_DB_ID={outcomes_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", required=True, help="Parent Notion page ID")
    args = parser.parse_args()

    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("Error: NOTION_TOKEN not set")
        sys.exit(1)

    create_databases(token, args.page_id)

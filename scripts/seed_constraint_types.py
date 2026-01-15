from __future__ import annotations

import os
import sys

from fateforger.adapters.notion.timeboxing_preferences import (
    NotionConstraintStore,
    get_notion_session,
    seed_default_constraint_types,
)


def main() -> int:
    parent_page_id = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "").strip()
    if not parent_page_id:
        print("Missing NOTION_TIMEBOXING_PARENT_PAGE_ID")
        return 1
    notion_token = os.getenv("NOTION_TOKEN")
    session = get_notion_session(notion_token=notion_token)
    store = NotionConstraintStore.from_parent_page(
        parent_page_id=parent_page_id,
        notion=session,
        write_registry_block=True,
    )
    pages = seed_default_constraint_types(store)
    print(f"Seeded/updated {len(pages)} constraint types.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

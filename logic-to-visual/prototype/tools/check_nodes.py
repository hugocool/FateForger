import base64

svg = open("constraint_flow.svg").read()
test_ids = [
    "extraction.slack",
    "extraction.llm_agent",
    "extraction.notion_extractor",
    "stores.notion_db",
    "stores.sqlite_db",
    "prefetch.date_commit",
    "prefetch.retriever",
    "prefetch.query_plan",
    "prefetch.type_ids",
    "prefetch.mcp_server",
    "filters.notion_store",
    "filters.date_filter",
    "filters.scope_filter",
    "filters.status_filter",
    "filters.stage_filter",
    "filters.event_filter",
    "filters.startup_tag",
    "postprocess.dedup",
    "postprocess.suppress",
    "postprocess.dow",
    "collect",
    "active",
    # containers
    "extraction",
    "stores",
    "prefetch",
    "filters",
    "postprocess",
]
for nid in test_ids:
    cls = base64.b64encode(nid.encode()).decode()
    found = cls in svg
    print(f"{'OK' if found else 'MISSING'} {nid!r:40} -> {cls!r}")

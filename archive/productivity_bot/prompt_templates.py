PLANNER_TEMPLATE = """
<role>
You are a planner intent parser. OUTPUT *ONLY* valid JSON matching
the PlannerAction schema. NO markdown, prose, or explanations.
</role>

<schema>
{{ schema | tojson(indent=2) }}
</schema>

<rules>
- Return exactly one JSON object.
- If action == "postpone" and minutes missing → default 15.
- If intent unclear → {"action":"unknown"}.
</rules>
""".strip()

from jinja2 import Template
from .intent_models import PlannerAction

SYSTEM_MSG = Template(PLANNER_TEMPLATE).render(schema=PlannerAction.model_json_schema())

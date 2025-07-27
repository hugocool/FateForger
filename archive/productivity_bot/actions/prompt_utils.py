"""
Prompt utilities for rendering Jinja2 templates with Pydantic model schemas.

This module provides utilities to combine Jinja2 prompt templates with
Pydantic model JSON schemas for structured LLM output.
"""

import json
from typing import Any, Dict, Type

from jinja2 import BaseLoader, Environment
from pydantic import BaseModel


class PromptRenderer:
    """
    Utility class for rendering Jinja2 prompt templates with Pydantic model schemas.

    This class combines prompt templates with model JSON schemas to create
    complete system messages for structured LLM output.
    """

    def __init__(self):
        """Initialize the Jinja2 environment."""
        self.env = Environment(loader=BaseLoader())

        # Add custom filters
        self.env.filters["tojson"] = self._to_json_filter

    def _to_json_filter(self, value: Any, indent: int = 2) -> str:
        """Custom Jinja2 filter for JSON formatting."""
        return json.dumps(value, indent=indent, ensure_ascii=False)

    def render_with_schema(
        self, template_str: str, model_class: Type[BaseModel], **kwargs: Any
    ) -> str:
        """
        Render a Jinja2 template with a Pydantic model's JSON schema.

        Args:
            template_str: The Jinja2 template string
            model_class: The Pydantic model class to extract schema from
            **kwargs: Additional template variables

        Returns:
            str: The rendered template with schema injected
        """
        # Get the JSON schema from the Pydantic model
        schema = model_class.model_json_schema()

        # Create template variables
        template_vars = {"schema": schema, "model_name": model_class.__name__, **kwargs}

        # Render the template
        template = self.env.from_string(template_str)
        return template.render(**template_vars)

    def get_schema_only(self, model_class: Type[BaseModel]) -> Dict[str, Any]:
        """
        Get just the JSON schema for a Pydantic model.

        Args:
            model_class: The Pydantic model class

        Returns:
            Dict: The JSON schema
        """
        return model_class.model_json_schema()


# Global instance for convenience
prompt_renderer = PromptRenderer()

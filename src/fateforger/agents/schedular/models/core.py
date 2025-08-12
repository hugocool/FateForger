from datetime import date as Date
from datetime import datetime, time
from enum import Enum, EnumMeta
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
    parse_obj_as,
)
from pydantic.json_schema import (
    GenerateJsonSchema,
    JsonSchemaValue,
)
from pydantic.annotated_handlers import GetJsonSchemaHandler
from pydantic_core import CoreSchema, PydanticOmit, core_schema

from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import Interval
from sqlalchemy.types import JSON as _JSON
from sqlalchemy.types import Enum as SQLEnum
from sqlalchemy.types import TypeDecorator
from sqlmodel import Enum as SQLModelEnum
from sqlmodel import Field as SQLModelField
from sqlmodel import Relationship, SQLModel
from functools import lru_cache
from datetime import timedelta

from pydantic import ValidationInfo
from sqlalchemy import Column, Time as SQLTime

from datetime import date as Date
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from isodate import parse_duration

# if TYPE_CHECKING:
#     from .calendar_event import CalendarEvent
from sqlalchemy.orm import Mapped, mapped_column
from sqlmodel import Field, Relationship, Session, SQLModel, select


class ChoiceField:
    """
    Descriptor for one choice: stores the raw code, a human description,
    and any extra metadata (e.g. color_id).
    """

    def __init__(self, value: str, *, description: str, **extras: Any):
        self.value = value
        self.description = description
        self.extras = extras


class ChoiceEnumMeta(EnumMeta):
    """
    Metaclass that:
    - Accepts `extra_field_kwargs=[...]` in the class header,
    - Strips out ChoiceField descriptors and replaces them with their `.value`,
    - Builds the Enum,
    - Gathers description+extras in a class‐level dict,
    - Dynamically attaches properties for each extra_field.
    """

    @classmethod
    def __prepare__(metacls, name, bases, *, extra_field_kwargs=None, **kwargs):
        # Delegate to EnumMeta for the special _EnumDict with _member_names support
        return super().__prepare__(name, bases)

    def __new__(
        metacls,
        name: str,
        bases: tuple,
        classdict: dict,
        *,
        extra_field_kwargs: List[str] | None = None,
        **kwargs,
    ):
        extra_fields = extra_field_kwargs or []

        # 1) Extract ChoiceField descriptors
        raw: Dict[str, ChoiceField] = {
            n: v for n, v in classdict.items() if isinstance(v, ChoiceField)
        }

        # 2) Replace each descriptor with its raw code for EnumMeta
        for n, cf in raw.items():
            # Remove the placeholder and its name from _member_names
            classdict.pop(n)
            if n in classdict._member_names:  # type: ignore
                classdict._member_names.remove(n)  # type: ignore
            # Insert the actual string value
            classdict[n] = cf.value

        # 3) Create the Enum class
        enum_cls = super().__new__(metacls, name, bases, classdict)

        # 4) Build a metadata map and attach to the class
        meta_map: Dict[str, Dict[str, Any]] = {}
        for n, cf in raw.items():
            missing = [f for f in extra_fields if f not in cf.extras]
            if missing:
                raise TypeError(f"{name}.{n} missing extras: {missing}")
            meta_map[n] = {"description": cf.description, **cf.extras}
        setattr(enum_cls, "_choice_meta_", meta_map)

        # 5) Dynamically add a property for each extra field
        for field in extra_fields:
            setattr(
                enum_cls,
                field,
                property(lambda self, f=field: self._choice_meta_[self.name][f]),
            )

        return enum_cls


class ChoiceEnum(str, Enum, metaclass=ChoiceEnumMeta):
    """
    - Behaves as a normal Enum at runtime (no multiple choice).
    - Validates as a string via Pydantic’s core schema hook.
    - Exposes a strict `oneOf` JSON schema with per-choice descriptions.
    - Provides dynamic properties for any `extra_field_kwargs`.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> CoreSchema:
        # 1) Parse as a string
        str_schema = core_schema.str_schema()

        # 2) After that, convert the raw string to your Enum member
        def _to_enum(v, info):
            return cls(v)

        post_schema = core_schema.with_info_after_validator_function(
            schema=str_schema, function=_to_enum
        )
        # 3) Return that as the core schema
        return post_schema

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> dict:
        return {
            "title": cls.__name__,
            "oneOf": [
                {
                    "const": m.value,
                    "title": m.name,
                    "description": cls._choice_meta_[m.name]["description"],
                    "type": "string",
                }
                for m in cls
            ],
        }

    def __str__(self) -> str:
        return self.value


def ORMField(*args: Any, include_in_schema: bool = True, **kwargs: Any) -> Any:
    """
    Wraps SQLModel’s Field() so you can pass `include_in_schema=False`
    and have it hidden from the generated JSON Schema.
    """
    # Only build schema_extra if the user really wants to hide this field
    if include_in_schema is False:
        # ensure we don't clobber any existing schema_extra
        existing = kwargs.pop("schema_extra", {}) or {}
        # only our flag
        existing.update({"include_in_schema": False})
        # wrap in the correct key for Pydantic v2+
        kwargs["schema_extra"] = {"json_schema_extra": existing}

    return SQLModelField(*args, **kwargs)


class LLMJsonSchema(GenerateJsonSchema):
    """
    Omit any field where json_schema_extra['include_in_schema'] is False.
    Otherwise include it by default.
    """

    def get_field_schema(
        self,
        field_name: str,
        core_schema: Any,
        field_schema: JsonSchemaValue | None = None,
        **kwargs: Any,
    ) -> JsonSchemaValue:
        fi = self.field_info_map[field_name].field_info
        # default to True if the flag is missing
        include = fi.schema_extra.get("include_in_schema", True)
        if not include:
            raise PydanticOmit
        return super().get_field_schema(
            field_name, core_schema, field_schema=field_schema, **kwargs
        )


from typing import Any

from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import CoreSchema


class LLMJsonSchema(GenerateJsonSchema):
    """
    Omit any field whose Field.schema_extra['include_in_schema'] is False,
    by short-circuiting Pydantic’s field_is_present test.
    """

    def field_is_present(self, fld: CoreSchema) -> bool:
        # 1) Let Pydantic decide if it even wants the field at all:
        if not super().field_is_present(fld):
            return False

        # 2) Our marker lives in the core-schema metadata under 'pydantic_js_extra'
        meta = fld.get("metadata") or {}
        js_extra = meta.get("pydantic_js_extra")

        # 3) If there's a dict and it has include_in_schema=False, drop it
        if isinstance(js_extra, dict) and js_extra.get("include_in_schema") is False:
            return False

        return True


class PydanticJSON(TypeDecorator):
    """
    SQLAlchemy TypeDecorator that transparently serialises / deserialises any
    Pydantic model (or list / dict of models) to a JSON column.  The concrete
    Pydantic class that should be reconstructed is provided via the
    ``model_class`` constructor argument.  On reads the raw JSON is converted
    back into the declared model with ``parse_obj_as`` so the SQLModel field
    retains its annotated type.
    """

    impl = _JSON
    cache_ok = True  # safe for SQLAlchemy’s type caching

    def __init__(
        self, model_class: Any
    ) -> (
        None
    ):  # model_class can be a BaseModel subclass or typing hints like List[Model]
        if not isinstance(model_class, type):
            # Handles typing constructs like List[Model] / Dict[str, Model]
            # They cannot be `issubclass`, but parse_obj_as will cope.
            self._model_factory = lambda obj: parse_obj_as(model_class, obj)
        elif issubclass(model_class, BaseModel):
            self._model_factory = lambda obj: model_class.model_validate(obj)
        else:
            raise TypeError(
                "model_class must be a Pydantic BaseModel or typing construct"
            )

        super().__init__()  # tyoe: ignore

    # convert Python -> JSON
    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            # Ensure all nested datetimes / complex types are JSON‑serialisable
            return value.model_dump(by_alias=True, mode="json")
        return value  # assume already JSON‑serialisable (e.g. dict / list)

    # convert JSON -> Python (typed)
    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self._model_factory(value)


# Helper to emit ISO8601 without milliseconds
def _iso_no_ms(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

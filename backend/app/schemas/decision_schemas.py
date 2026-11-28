from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttributeCreate(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    label: str = Field(min_length=1, max_length=80)
    value_type: Literal["number", "text", "boolean", "text_list"]
    unit: Optional[str] = Field(default=None, max_length=20)
    category: str
    description: Optional[str] = Field(default=None, max_length=300)


class AttributeUpdate(_Strict):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)
    category: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None


class ValueSet(_Strict):
    value: Any
    source_note: str = Field(min_length=3, max_length=200)


class ConstraintCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["hard", "soft"]
    category: str
    ref: str = Field(max_length=80)
    operator: str = Field(max_length=20)
    value: Any
    unit: Optional[str] = Field(default=None, max_length=20)
    rationale: str = Field(min_length=3, max_length=400)


class ConstraintUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    kind: Optional[Literal["hard", "soft"]] = None
    category: Optional[str] = None
    ref: Optional[str] = Field(default=None, max_length=80)
    operator: Optional[str] = Field(default=None, max_length=20)
    value: Optional[Any] = None
    unit: Optional[str] = Field(default=None, max_length=20)
    rationale: Optional[str] = Field(default=None, min_length=3, max_length=400)
    status: Optional[Literal["active", "archived"]] = None


class ObjectiveCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    ref: str = Field(max_length=80)
    direction: Literal["maximize", "minimize"]


class ObjectiveUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    direction: Optional[Literal["maximize", "minimize"]] = None
    status: Optional[Literal["active", "archived"]] = None

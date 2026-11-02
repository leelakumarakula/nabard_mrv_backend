import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel

ApprovalStatus = Literal["proposed", "approved", "retired"]


class CategoryIn(BaseModel):
    code: str = Field(min_length=2, max_length=60, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=2, max_length=160)
    description: str | None = None
    definition: str | None = None
    is_sensitive: bool = False
    requires_consent: bool = True
    status: ApprovalStatus = "proposed"
    approval_note: str | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    definition: str | None = None
    is_sensitive: bool | None = None
    requires_consent: bool | None = None
    status: ApprovalStatus | None = None
    approval_note: str | None = None
    sort_order: int | None = None


class CategoryOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    definition: str | None
    is_sensitive: bool
    requires_consent: bool
    status: str
    approval_note: str | None
    sort_order: int


class AssignmentIn(BaseModel):
    category_code: str
    basis: Literal["self_declared", "document", "field_verified", "other"] = "self_declared"
    assigned_on: date | None = None
    note: str | None = None


class AssignmentUpdate(BaseModel):
    status: Literal["active", "removed"] | None = None
    basis: Literal["self_declared", "document", "field_verified", "other"] | None = None
    note: str | None = None


class AssignmentOut(ORMModel):
    id: uuid.UUID
    farmer_id: uuid.UUID
    category_id: uuid.UUID
    category_code: str
    category_name: str
    is_sensitive: bool
    status: str
    basis: str
    assigned_on: date | None
    note: str | None


class DemographicFieldIn(BaseModel):
    code: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=2, max_length=160)
    field_type: Literal["text", "select", "boolean", "number", "date"]
    options: list[str] | None = None
    is_sensitive: bool = False
    requires_consent: bool = True
    status: ApprovalStatus = "proposed"
    help_text: str | None = None
    sort_order: int = 0

    @model_validator(mode="after")
    def options_for_select(self) -> "DemographicFieldIn":
        if self.field_type == "select" and not self.options:
            raise ValueError("A select field needs options")
        return self


class DemographicFieldUpdate(BaseModel):
    label: str | None = None
    options: list[str] | None = None
    is_sensitive: bool | None = None
    requires_consent: bool | None = None
    status: ApprovalStatus | None = None
    help_text: str | None = None
    sort_order: int | None = None


class DemographicFieldOut(ORMModel):
    id: uuid.UUID
    code: str
    label: str
    field_type: str
    options: list[str] | None
    is_sensitive: bool
    requires_consent: bool
    status: str
    help_text: str | None
    sort_order: int


REPORT_SECTIONS = ["planning_targets", "progress_vs_targets", "funnel", "coverage", "inclusion", "demonstrations",
                   "surveillance", "indicator_results", "data_quality", "module_activity", "mrv", "verification"]


class TemplateIn(BaseModel):
    code: str = Field(min_length=2, max_length=60, pattern=r"^[A-Z0-9_]+$")
    name: str
    description: str | None = None
    base_report_type: str
    sections: list[str] = []
    status: Literal["draft", "approved", "awaiting_official_template", "retired"] = "draft"
    is_official: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def known_sections(self) -> "TemplateIn":
        unknown = set(self.sections) - set(REPORT_SECTIONS)
        if unknown:
            raise ValueError(f"Unknown section(s): {', '.join(sorted(unknown))}")
        return self


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sections: list[str] | None = None
    status: Literal["draft", "approved", "awaiting_official_template", "retired"] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def known_sections(self) -> "TemplateUpdate":
        unknown = set(self.sections or []) - set(REPORT_SECTIONS)
        if unknown:
            raise ValueError(f"Unknown section(s): {', '.join(sorted(unknown))}")
        return self


class TemplateOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    base_report_type: str
    sections: list[str]
    status: str
    is_official: bool
    notes: str | None


class DecisionIn(BaseModel):
    programme_id: uuid.UUID | None = None
    code: str = Field(min_length=2, max_length=60)
    title: str
    question: str
    category: Literal["scope", "indicators", "inclusion", "field", "verification", "reporting", "integration", "privacy"]
    provisional_treatment: str | None = None
    owner: str | None = None
    affects: list[str] = []


class DecisionUpdate(BaseModel):
    title: str | None = None
    question: str | None = None
    status: Literal["pending", "decided", "superseded"] | None = None
    provisional_treatment: str | None = None
    decision: str | None = None
    decided_by: str | None = None
    decided_on: date | None = None
    owner: str | None = None
    affects: list[str] | None = None

    @model_validator(mode="after")
    def decided_needs_detail(self) -> "DecisionUpdate":
        if self.status == "decided" and not (self.decision and self.decided_by and self.decided_on):
            raise ValueError("Record the decision, who decided and when")
        return self


class DecisionOut(ORMModel):
    id: uuid.UUID
    programme_id: uuid.UUID | None
    code: str
    title: str
    question: str
    category: str
    status: str
    provisional_treatment: str | None
    decision: str | None
    decided_by: str | None
    decided_on: date | None
    owner: str | None
    affects: list[str]
    updated_at: datetime


class ChecklistItem(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str
    required: bool = False


class Assessment(BaseModel):
    """Answers to a configured checklist: {code: {"met"/"ok": bool, "note": str}}."""

    items: dict[str, dict[str, Any]]

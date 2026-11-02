import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

PayloadKind = Literal["measurements", "observations", "advisories"]


class SourceIn(BaseModel):
    source_code: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_]+$")
    name: str
    module_code: str | None = None
    source_type: Literal["rest_push", "rest_pull", "file_exchange"]
    adapter_code: Literal["push_only", "mock_water_meter", "mock_iot_gateway", "unconfigured_rest"] = "push_only"
    base_url_env_var: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    auth_type: Literal["api_key", "oauth2", "basic", "none"] = "api_key"
    owner: str | None = None
    enabled: bool = True
    is_mock: bool = False
    availability_note: str | None = None
    config: dict[str, Any] = {}


class SourceUpdate(BaseModel):
    name: str | None = None
    module_code: str | None = None
    adapter_code: Literal["push_only", "mock_water_meter", "mock_iot_gateway", "unconfigured_rest"] | None = None
    base_url_env_var: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    auth_type: Literal["api_key", "oauth2", "basic", "none"] | None = None
    owner: str | None = None
    enabled: bool | None = None
    availability_note: str | None = None
    config: dict[str, Any] | None = None


class SourceOut(ORMModel):
    id: uuid.UUID
    source_code: str
    name: str
    module_code: str | None
    source_type: str
    adapter_code: str
    base_url_env_var: str | None
    auth_type: str
    owner: str | None
    enabled: bool
    is_mock: bool
    availability_note: str | None
    inbound_key_hint: str | None
    config: dict[str, Any]
    created_at: datetime
    connection_status: str = "unknown"
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    open_quarantine: int = 0


class KeyOut(BaseModel):
    source_code: str
    inbound_key: str
    message: str


class MappingIn(BaseModel):
    payload_kind: PayloadKind
    source_field: str = Field(min_length=1, max_length=120)
    target_field: str = Field(min_length=1, max_length=120)
    value_map: dict[str, Any] | None = None
    notes: str | None = None


class MappingOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    payload_kind: str
    source_field: str
    target_field: str
    value_map: dict[str, Any] | None
    notes: str | None


class InboundBatch(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class TestPayloadIn(BaseModel):
    payload_kind: PayloadKind
    records: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class SyncRunOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    payload_kind: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    records_read: int
    records_accepted: int
    records_rejected: int
    records_duplicate: int
    cursor: str | None
    error_summary: str | None
    correlation_id: str | None
    replay_of_id: uuid.UUID | None
    triggered_by_id: uuid.UUID | None


class BatchResultOut(BaseModel):
    sync_run: SyncRunOut
    outcomes: list[dict[str, Any]]


class QuarantineOut(ORMModel):
    id: uuid.UUID
    sync_run_id: uuid.UUID
    source_id: uuid.UUID
    payload_kind: str
    source_record_id: str | None
    payload: dict[str, Any]
    errors: list[str]
    status: str
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class ReplayIn(BaseModel):
    quarantine_ids: list[uuid.UUID] | None = None
    sync_run_id: uuid.UUID | None = None


class DiscardIn(BaseModel):
    note: str = Field(min_length=3)


class PlotMapIn(BaseModel):
    source_plot_id: str = Field(min_length=1, max_length=120)
    farm_plot_id: uuid.UUID


class IdentityMapIn(BaseModel):
    entity_type: Literal["farm_plot", "farmer", "device"]
    source_id: str = Field(min_length=1, max_length=120)
    internal_id: uuid.UUID
    review_note: str | None = None


class IdentityMapOut(ORMModel):
    id: uuid.UUID
    entity_type: str
    source_record_id: str
    internal_record_id: uuid.UUID
    mapped_by_id: uuid.UUID | None
    review_note: str | None
    first_seen_at: datetime

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Mode(str, Enum):
    OFF = "off"
    GUARD_ONLY = "guard_only"
    FULL = "full"


class ResourceType(str, Enum):
    APP = "app"
    STATE = "state"


class CallContext(BaseModel):
    user_goal: str
    trusted_system_goal: str
    source_summary: str = ""
    external_text: str = ""
    scenario_id: str | None = None


class CallAppRequest(BaseModel):
    request_id: str
    session_id: str
    mode: Mode
    did: str
    resource_type: ResourceType
    app: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    context: CallContext
    payload_hash: str
    signature: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GuardDecision(BaseModel):
    allowed: bool
    risk_type: str
    intent_similarity: float
    threshold: float
    gray_zone: tuple[float, float] | None = None
    embedding_model: str
    embedding_backend: str
    reranker_model: str | None = None
    reranker_backend: str | None = None
    reranker_score: float | None = None
    reranker_threshold: float | None = None
    decision_stage: str
    reason: str
    confidence: str
    trust_labels: dict[str, str] = Field(default_factory=dict)


class AuthResult(BaseModel):
    verified: bool
    permission_allowed: bool
    backend: str
    reason: str
    permission_key: str
    chain_available: bool
    block_number: int | None = None


class ProtectedStateDiff(BaseModel):
    target: str
    old_hash: str
    baseline_hash: str
    new_hash: str
    diff: str
    suspicious: bool
    approval_required: bool
    approval_granted: bool
    baseline_drift_detected: bool = False
    risk_labels: list[str] = Field(default_factory=list)
    rollback_performed: bool = False
    applied: bool = False


class AppCallResult(BaseModel):
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class AuditView(BaseModel):
    request_id: str
    entry_hash: str
    previous_hash: str
    chain_receipt: str | None = None
    chain_backend: str | None = None
    chain_block_number: int | None = None


class ChainStatusView(BaseModel):
    available: bool
    backend: str
    block_number: int | None = None
    message: str
    checked_at: str
    endpoints: list[str] = Field(default_factory=list)


class CallAppResponse(BaseModel):
    request_id: str
    mode: Mode
    allowed: bool
    status: str
    blocked_layer: str | None = None
    message: str
    result: AppCallResult | None = None
    guard: GuardDecision | None = None
    auth: AuthResult | None = None
    audit: AuditView | None = None
    state_change: ProtectedStateDiff | None = None
    latency_ms: float


class ScenarioStep(BaseModel):
    resource_type: ResourceType
    app: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScenarioDefinition(BaseModel):
    scenario_id: str
    scenario_type: str
    title: str
    description: str
    user_task: str
    trusted_system_goal: str
    source_summary: str
    external_text: str = ""
    steps: list[ScenarioStep]
    expected_by_mode: dict[str, str]


class ExecutionTrace(BaseModel):
    step_index: int
    request: CallAppRequest
    response: CallAppResponse


class DemoRunRequest(BaseModel):
    scenario_id: str
    mode: Mode
    session_id: str = "demo-session"
    use_real_openclaw: bool = False
    approval_token: str | None = None
    user_task_override: str | None = None


class ManualStateUpdateRequest(BaseModel):
    target: str
    content: str
    mode: Mode
    approval_token: str | None = None


class DemoRunResponse(BaseModel):
    scenario: ScenarioDefinition
    mode: Mode
    assistant_reply: str
    traces: list[ExecutionTrace]
    final_status: str
    blocked_layer: str | None = None
    summary: str


class ExperimentRecord(BaseModel):
    sample_id: str
    scenario_type: str
    mode: Mode
    request_id: str
    expected_result: str
    actual_result: str
    blocked_layer: str | None
    latency_ms: float
    false_positive: bool
    audit_written: bool
    intent_similarity: float | None = None
    chain_backend: str | None = None
    chain_available: bool | None = None
    state_alert: bool = False


class ExperimentAggregate(BaseModel):
    mode: Mode
    total: int
    blocked: int
    executed: int
    correct: int
    false_positive_rate: float
    interception_rate: float
    state_detection_rate: float
    avg_latency_ms: float


class ExperimentReport(BaseModel):
    records: list[ExperimentRecord]
    aggregates: list[ExperimentAggregate]
    exported_json: str
    exported_csv: str

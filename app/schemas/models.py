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


class OpenClawToolCall(BaseModel):
    tool_name: str = "call_app_api"
    resource_type: ResourceType
    app: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenClawPlan(BaseModel):
    assistant_reply: str
    calls: list[OpenClawToolCall]
    backend: str
    raw_response: str | None = None


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
    degraded_allowed: bool = False


class ProtectedStateDiff(BaseModel):
    target: str
    old_hash: str
    baseline_hash: str
    new_hash: str
    diff: str
    suspicious: bool
    approval_required: bool
    approval_granted: bool
    approval_token: str | None = None
    approval_status: str | None = None
    approval_decision_by: str | None = None
    approval_note: str | None = None
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
    permission_key: str | None = None
    risk_level: str | None = None
    approval_required: bool = False


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


class ApprovalDecisionRequest(BaseModel):
    token: str
    decision: str = Field(pattern="^(approve|reject)$")
    approver_did: str
    note: str = ""


class ApprovalView(BaseModel):
    token: str
    request_id: str
    payload_hash: str
    target: str
    diff: str
    risk_labels: list[str] = Field(default_factory=list)
    status: str
    issued_at: str
    decided_at: str | None = None
    decision_by: str | None = None
    decision_note: str | None = None


class DemoRunResponse(BaseModel):
    scenario: ScenarioDefinition
    mode: Mode
    assistant_reply: str
    openclaw_plan: OpenClawPlan
    traces: list[ExecutionTrace]
    final_status: str
    blocked_layer: str | None = None
    summary: str


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalView


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
    reranker_score: float | None = None
    permission_key: str | None = None
    reason: str | None = None
    chain_backend: str | None = None
    chain_available: bool | None = None
    state_alert: bool = False
    approval_required: bool = False
    approval_granted: bool = False
    approval_status: str | None = None

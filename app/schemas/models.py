from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Mode(str, Enum):
    OFF = "off"
    GUARD_ONLY = "guard_only"
    FULL = "full"


class PlannerMode(str, Enum):
    REAL_STRICT = "real_strict"
    REAL_DEBUG = "real_debug"
    DEMO_SAFE = "demo_safe"


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
    model_config = ConfigDict(extra="forbid")

    tool_name: str = "call_app_api"
    resource_type: ResourceType
    app: str
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenClawPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str
    calls: list[OpenClawToolCall]
    backend: str
    planner_mode: PlannerMode = PlannerMode.REAL_STRICT
    planning_source: str = "openclaw"
    planning_valid: bool = True
    planning_error: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    plan_hash: str | None = None
    planning_session_id: str | None = None
    planning_prompt: str | None = None
    raw_response: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


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


class UnifiedLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    timestamp: str
    source: str
    kind: str
    title: str
    summary: str
    request_id: str | None = None
    status: str | None = None
    blocked_layer: str | None = None
    block_number: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


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


class PlannerResourceHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    app: str | None = None
    resource_id: str | None = None
    description: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class PlanningConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant_apps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    resource_hints: list[PlannerResourceHint] = Field(default_factory=list)


class EvaluationOracle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_by_mode: dict[str, str]
    expected_actions: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ScenarioDebugPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str | None = None
    calls: list[OpenClawToolCall] = Field(default_factory=list)


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_type: str
    title: str
    description: str
    user_task: str
    trusted_system_goal: str
    source_summary: str
    external_text: str = ""
    planning_constraints: PlanningConstraints = Field(default_factory=PlanningConstraints)
    evaluation_oracle: EvaluationOracle
    debug_plan: ScenarioDebugPlan | None = None
    request_mutation: str | None = None
    chain_fault: str | None = None

    @property
    def expected_by_mode(self) -> dict[str, str]:
        return self.evaluation_oracle.expected_by_mode


class DashboardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openclaw_status: dict[str, Any]
    chain_status: dict[str, Any]
    state: dict[str, Any]
    pending_approvals: list[ApprovalView] = Field(default_factory=list)
    logs: list[UnifiedLogEntry] = Field(default_factory=list)
    event_seq: int = 0


class ClientLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    level: str = "info"
    message: str
    source: str = "frontend"
    url: str | None = None
    page: str | None = None
    request_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ClientLogBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ClientLogEvent] = Field(default_factory=list)


class ExecutionTrace(BaseModel):
    step_index: int
    request: CallAppRequest
    response: CallAppResponse


class DemoRunRequest(BaseModel):
    scenario_id: str
    mode: Mode
    session_id: str = "demo-session"
    planner_mode: PlannerMode = PlannerMode.REAL_STRICT
    include_mode_compare: bool = False
    approval_token: str | None = None
    user_task_override: str | None = None


class ManualStateUpdateRequest(BaseModel):
    target: str
    content: str
    mode: Mode
    approval_token: str | None = None


class ExperimentRunRequest(BaseModel):
    planner_mode: PlannerMode = PlannerMode.REAL_STRICT
    max_planning_failures: int | None = Field(default=6, ge=1)


class ApprovalDecisionRequest(BaseModel):
    token: str
    decision: str = Field(pattern="^(approve|reject)$")
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


class PlanAssessment(BaseModel):
    planned_actions: list[str] = Field(default_factory=list)
    matched_expected_actions: list[str] = Field(default_factory=list)
    missing_expected_actions: list[str] = Field(default_factory=list)
    triggered_prohibited_actions: list[str] = Field(default_factory=list)
    matches_oracle: bool = True


class ScenarioPublicView(BaseModel):
    scenario_id: str
    scenario_type: str
    title: str
    description: str
    user_task: str


class DemoRunResponse(BaseModel):
    scenario: ScenarioPublicView
    mode: Mode
    assistant_reply: str
    openclaw_plan: OpenClawPlan
    plan_assessment: PlanAssessment
    traces: list[ExecutionTrace]
    final_status: str
    blocked_layer: str | None = None
    summary: str
    evidence_summary: str | None = None
    planning_failed: bool = False
    planning_failed_reason: str | None = None
    mode_compare: list["ModeCompareResult"] = Field(default_factory=list)


class ModeCompareResult(BaseModel):
    mode: Mode
    final_status: str
    blocked_layer: str | None = None
    request_id: str | None = None
    summary: str
    evidence_summary: str | None = None
    openclaw_backend: str | None = None
    planning_failed: bool = False
    planning_failed_reason: str | None = None


class RequestTraceBundle(BaseModel):
    request_id: str
    source: str
    assistant_reply: str | None = None
    openclaw_backend: str | None = None
    planner_mode: PlannerMode = PlannerMode.REAL_STRICT
    planning_source: str | None = None
    planning_valid: bool = True
    planning_error: str | None = None
    planning_diagnostics: dict[str, Any] = Field(default_factory=dict)
    plan_hash: str | None = None
    planning_session_id: str | None = None
    planning_prompt: str | None = None
    openclaw_raw_response: str | None = None
    traces: list[ExecutionTrace] = Field(default_factory=list)
    final_status: str | None = None
    blocked_layer: str | None = None
    summary: str | None = None
    evidence_summary: str | None = None
    planning_failed: bool = False
    planning_failed_reason: str | None = None
    mode_compare: list[ModeCompareResult] = Field(default_factory=list)


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalView
    auth: AuthResult | None = None
    audit: AuditView | None = None


class ExperimentRecord(BaseModel):
    sample_id: str
    scenario_type: str
    mode: Mode
    request_id: str
    planner_mode: PlannerMode = PlannerMode.REAL_STRICT
    planning_source: str | None = None
    planning_valid: bool = True
    planning_error: str | None = None
    planning_diagnostics: dict[str, Any] = Field(default_factory=dict)
    plan_hash: str | None = None
    planning_session_id: str | None = None
    plan_replay_group: str | None = None
    plan_reused_across_modes: bool = False
    plan_hash_consistent: bool | None = None
    planned_actions: list[str] = Field(default_factory=list)
    plan_matches_oracle: bool = True
    missing_expected_actions: list[str] = Field(default_factory=list)
    triggered_prohibited_actions: list[str] = Field(default_factory=list)
    expected_result: str
    actual_result: str
    blocked_layer: str | None
    latency_ms: float
    false_positive: bool
    audit_written: bool
    planning_failed: bool = False
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
    rollback_performed: bool = False
    guard_decision_stage: str | None = None
    auth_reason: str | None = None
    risk_level: str | None = None
    plan_backend: str | None = None
    degraded_allowed: bool = False


class ExperimentAggregate(BaseModel):
    mode: Mode
    total: int
    blocked: int
    executed: int
    planning_failed: int = 0
    correct: int
    false_positive_rate: float
    interception_rate: float
    state_detection_rate: float
    avg_latency_ms: float
    planning_failed_rate: float = 0.0
    plan_hash_consistency_rate: float = 0.0


class ExperimentReport(BaseModel):
    records: list[ExperimentRecord]
    aggregates: list[ExperimentAggregate]
    run_id: str | None = None
    total_scenarios: int | None = None
    total_runs: int | None = None
    planning_failures: int = 0
    max_planning_failures: int | None = None
    early_terminated: bool = False
    termination_reason: str | None = None
    paper_ablation_runs: int | None = None
    exported_dir: str | None = None
    exported_json: str
    exported_csv: str
    manifest_json: str | None = None
    responses_json: str | None = None
    traces_json: str | None = None
    audit_json: str | None = None
    chain_json: str | None = None
    scenarios_json: str | None = None
    paper_ablation_json: str | None = None
    paper_ablation_csv: str | None = None


DemoRunResponse.model_rebuild()
RequestTraceBundle.model_rebuild()

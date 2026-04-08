from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.actions import state_action_for_target
from app.core.openclaw import OpenClawPlanningError
from app.core.utils import sha256_text
from app.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    AuditView,
    AuthResult,
    CallAppRequest,
    CallContext,
    DemoRunRequest,
    ExecutionTrace,
    ManualStateUpdateRequest,
    OpenClawToolCall,
    RequestTraceBundle,
    ResourceType,
)


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def record_direct_trace(container, payload: CallAppRequest, response, *, source: str) -> None:
        container.audit.record_request_trace(
            RequestTraceBundle(
                request_id=payload.request_id,
                source=source,
                assistant_reply=None,
                openclaw_backend=None,
                openclaw_raw_response=None,
                traces=[ExecutionTrace(step_index=1, request=payload, response=response)],
                final_status=response.status,
                blocked_layer=response.blocked_layer,
                summary=response.message,
                mode_compare=[],
            ).model_dump(mode="json")
        )

    def build_manual_state_call(container, payload: ManualStateUpdateRequest) -> CallAppRequest:
        action = state_action_for_target(payload.target)
        request_seed = "\n".join([payload.target, payload.content, payload.mode.value])
        request_id = f"manual-state-{sha256_text(request_seed)[:16]}"
        call = container.builder.build(
            session_id="manual-state",
            mode=payload.mode,
            intent=OpenClawToolCall(
                resource_type=ResourceType.STATE,
                app="state",
                action=action,
                args={"target": payload.target, "content": payload.content},
                metadata={"approval_token": payload.approval_token} if payload.approval_token else {},
            ),
            context=CallContext(
                user_goal="手动更新受保护状态",
                trusted_system_goal=action,
                source_summary="demo console state editor",
                external_text="",
                scenario_id=None,
            ),
            request_id=request_id,
        )
        if payload.approval_token:
            call.metadata["approval_token"] = payload.approval_token
        return call

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        app_state = request.app.state.container
        scenario_items = app_state.orchestrator.scenarios()
        openclaw_status = await app_state.orchestrator.openclaw.status()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "scenarios": scenario_items,
                "scenario_payloads": [scenario.model_dump(mode="json") for scenario in scenario_items],
                "recent_audit": app_state.audit.recent(12),
                "state_targets": app_state.state_store.list_targets(),
                "identity": app_state.chain.demo_identity(),
                "approver_identity": app_state.chain.approver_identity(),
                "chain_status": app_state.chain.status_view(force_refresh=True),
                "openclaw_status": openclaw_status.__dict__,
                "pending_approvals": [item.model_dump(mode="json") for item in app_state.approvals.list_pending()],
            },
        )

    @router.get("/api/scenarios")
    async def scenarios(request: Request) -> dict:
        return {"items": [scenario.model_dump(mode="json") for scenario in request.app.state.container.orchestrator.scenarios()]}

    @router.post("/api/call_app")
    async def call_app(request: Request, payload: CallAppRequest) -> dict:
        container = request.app.state.container
        response = container.gateway.handle_call(payload)
        record_direct_trace(container, payload, response, source="direct_call")
        return response.model_dump(mode="json")

    @router.post("/api/demo/run")
    async def run_demo(request: Request, payload: DemoRunRequest) -> dict:
        try:
            response = await request.app.state.container.orchestrator.run_demo(payload)
        except OpenClawPlanningError as exc:
            raise HTTPException(status_code=502, detail=f"openclaw planning failed: {exc}") from exc
        return response.model_dump(mode="json")

    @router.post("/api/state/update")
    async def update_state(request: Request, payload: CallAppRequest) -> dict:
        container = request.app.state.container
        if payload.resource_type != ResourceType.STATE or payload.app != "state":
            raise HTTPException(status_code=422, detail="state update endpoint only accepts signed state CallAppRequest")
        try:
            expected_action = state_action_for_target(str(payload.args.get("target", "")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.action != expected_action:
            raise HTTPException(status_code=422, detail="state action does not match target")
        response = container.gateway.handle_call(payload)
        record_direct_trace(container, payload, response, source="state_update")
        return response.model_dump(mode="json")

    @router.post("/api/demo/state/request")
    async def build_state_request(request: Request, payload: ManualStateUpdateRequest) -> dict:
        container = request.app.state.container
        call = build_manual_state_call(container, payload)
        return call.model_dump(mode="json")

    @router.get("/api/approvals/pending")
    async def pending_approvals(request: Request) -> dict:
        container = request.app.state.container
        return {"items": [item.model_dump(mode="json") for item in container.approvals.list_pending()]}

    @router.post("/api/approvals/decision")
    async def decide_approval(request: Request, payload: ApprovalDecisionRequest) -> dict:
        container = request.app.state.container
        pending = container.approvals.get(payload.token)
        if pending is None:
            raise HTTPException(status_code=404, detail="approval token not found")
        approver = container.chain.approver_identity()
        auth_payload = {
            "request_id": pending.request_id,
            "approval_token": pending.token,
            "decision": payload.decision,
            "note": payload.note,
        }
        signature = container.signer.sign_payload(auth_payload, approver.private_key)
        auth = container.chain.authorize(auth_payload, signature, approver.did, "approval.decide")
        auth_result = AuthResult(
            verified=auth.verified,
            permission_allowed=auth.permission_allowed,
            backend=auth.backend,
            reason=auth.reason,
            permission_key=auth.permission_key,
            chain_available=auth.chain_available,
            block_number=auth.block_number,
            degraded_allowed=False,
        )
        if not auth.chain_available:
            raise HTTPException(status_code=503, detail=f"approval chain unavailable: {auth.reason}")
        if not auth.verified or not auth.permission_allowed:
            raise HTTPException(status_code=403, detail=auth.reason)
        approval = container.approvals.decide(payload.token, payload.decision, approver.did, payload.note)
        audit_entry = container.audit.record_approval(
            {
                "request_id": approval.request_id,
                "approval_token": approval.token,
                "status": approval.status,
                "target": approval.target,
                "decision_by": approval.decision_by,
                "decision_note": approval.decision_note,
                "auth": auth_result.model_dump(mode="json"),
            }
        )
        chain_receipt = None
        chain_backend = None
        chain_block_number = None
        try:
            chain_receipt, chain_block_number, chain_backend = container.chain.record_audit(
                approval.request_id,
                {
                    "status": approval.status,
                    "blocked_layer": "approval",
                    "app": "approval",
                    "action": "decide",
                    "message": f"{approval.status} by {approval.decision_by}",
                },
            )
        except Exception as exc:
            chain_backend = f"audit_unavailable:{type(exc).__name__}"
        response = ApprovalDecisionResponse(
            approval=approval,
            auth=auth_result,
            audit=AuditView(
                request_id=approval.request_id,
                entry_hash=audit_entry["entry_hash"],
                previous_hash=audit_entry["previous_hash"],
                chain_receipt=chain_receipt,
                chain_backend=chain_backend,
                chain_block_number=chain_block_number,
            ),
        )
        return response.model_dump(mode="json")

    @router.post("/api/experiments/run")
    async def run_experiments(request: Request) -> dict:
        response = await request.app.state.container.orchestrator.run_experiments()
        return response.model_dump(mode="json")

    @router.get("/api/audit/recent")
    async def recent_audit(request: Request, request_id: str | None = None) -> dict:
        container = request.app.state.container
        audit = container.audit.recent(30)
        chain = container.chain.audit_records(30)
        approvals = container.approvals.list_pending()
        if request_id:
            audit = [item for item in audit if item.get("request_id") == request_id]
            chain = [item for item in chain if item.get("request_id") == request_id]
            approvals = [item for item in approvals if item.request_id == request_id]
        return {
            "audit": audit,
            "chain": chain,
            "approvals": [item.model_dump(mode="json") for item in approvals],
            "request_trace": container.audit.request_trace(request_id) if request_id else None,
        }

    @router.get("/api/chain/status")
    async def chain_status(request: Request) -> dict:
        container = request.app.state.container
        return container.chain.status_view(force_refresh=True).model_dump(mode="json")

    @router.get("/api/openclaw/status")
    async def openclaw_status(request: Request) -> dict:
        status = await request.app.state.container.orchestrator.openclaw.status()
        return {
            "available": status.available,
            "backend": status.backend,
            "message": status.message,
            "checked_at": status.checked_at,
            "url": status.url,
        }

    @router.get("/api/state")
    async def state_snapshot(request: Request) -> dict:
        container = request.app.state.container
        return {
            "targets": container.state_store.list_targets(),
            "baselines": container.integrity.baselines(),
            "drift": container.integrity.drift_report(),
        }

    return router

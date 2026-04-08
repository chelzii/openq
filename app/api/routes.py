from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.schemas import ApprovalDecisionRequest, CallAppRequest, DemoRunRequest


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        app_state = request.app.state.container
        scenario_items = app_state.orchestrator.scenarios()
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
                "pending_approvals": [item.model_dump(mode="json") for item in app_state.approvals.list_pending()],
            },
        )

    @router.get("/api/scenarios")
    async def scenarios(request: Request) -> dict:
        return {"items": [scenario.model_dump(mode="json") for scenario in request.app.state.container.orchestrator.scenarios()]}

    @router.post("/api/call_app")
    async def call_app(request: Request, payload: CallAppRequest) -> dict:
        response = request.app.state.container.gateway.handle_call(payload)
        return response.model_dump(mode="json")

    @router.post("/api/demo/run")
    async def run_demo(request: Request, payload: DemoRunRequest) -> dict:
        response = await request.app.state.container.orchestrator.run_demo(payload)
        return response.model_dump(mode="json")

    @router.post("/api/state/update")
    async def update_state(request: Request, payload: CallAppRequest) -> dict:
        if payload.resource_type.value != "state" or payload.app != "state":
            raise HTTPException(status_code=400, detail="state update endpoint only accepts resource_type=state requests")
        response = request.app.state.container.gateway.handle_call(payload)
        return response.model_dump(mode="json")

    @router.get("/api/approvals/pending")
    async def pending_approvals(request: Request) -> dict:
        container = request.app.state.container
        return {"items": [item.model_dump(mode="json") for item in container.approvals.list_pending()]}

    @router.post("/api/approvals/decision")
    async def decide_approval(request: Request, payload: ApprovalDecisionRequest) -> dict:
        container = request.app.state.container
        approver = container.chain.approver_identity()
        if payload.approver_did != approver.did:
            raise HTTPException(status_code=403, detail="approval decision must be submitted by the registered approver DID")
        approval = container.approvals.decide(payload.token, payload.decision, payload.approver_did, payload.note)
        container.audit.record_approval(
            {
                "request_id": approval.request_id,
                "approval_token": approval.token,
                "status": approval.status,
                "target": approval.target,
                "decision_by": approval.decision_by,
                "decision_note": approval.decision_note,
            }
        )
        return {"approval": approval.model_dump(mode="json")}

    @router.post("/api/experiments/run")
    async def run_experiments(request: Request) -> dict:
        response = await request.app.state.container.orchestrator.run_experiments()
        return response.model_dump(mode="json")

    @router.get("/api/audit/recent")
    async def recent_audit(request: Request) -> dict:
        container = request.app.state.container
        return {
            "audit": container.audit.recent(30),
            "chain": container.chain.audit_records(30),
        }

    @router.get("/api/chain/status")
    async def chain_status(request: Request) -> dict:
        container = request.app.state.container
        return container.chain.status_view(force_refresh=True).model_dump(mode="json")

    @router.get("/api/state")
    async def state_snapshot(request: Request) -> dict:
        container = request.app.state.container
        return {
            "targets": container.state_store.list_targets(),
            "baselines": container.integrity.baselines(),
            "drift": container.integrity.drift_report(),
        }

    return router

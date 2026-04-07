from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.schemas import CallAppRequest, DemoRunRequest, ManualStateUpdateRequest


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
                "chain_status": app_state.chain.status_view(force_refresh=True),
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
    async def update_state(request: Request, payload: ManualStateUpdateRequest) -> dict:
        container = request.app.state.container
        identity = container.chain.demo_identity()
        signed_payload = {
            "request_id": f"manual-state-{__import__('time').time_ns()}",
            "session_id": "manual-state",
            "mode": payload.mode.value,
            "did": identity.did,
            "resource_type": "state",
            "app": "state",
            "action": "update_memory"
            if payload.target.startswith("memory/")
            else "update_prompt"
            if payload.target.startswith("prompt/")
            else "update_config",
            "args": {"target": payload.target, "content": payload.content},
            "context": {
                "user_goal": "手动更新受保护状态",
                "trusted_system_goal": "update_protected_state",
                "source_summary": "",
                "external_text": "",
                "scenario_id": None,
            },
            "metadata": {},
        }
        payload_hash = container.signer.payload_hash(signed_payload)
        signature = container.signer.sign_payload(signed_payload, identity.private_key)
        call = CallAppRequest.model_validate({**signed_payload, "payload_hash": payload_hash, "signature": signature})
        response = container.gateway.handle_call(call, approval_token=payload.approval_token)
        return response.model_dump(mode="json")

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

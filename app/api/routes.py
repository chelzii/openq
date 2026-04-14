from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from app.core.actions import state_action_for_target
from app.core.openclaw import OpenClawPlanningError
from app.core.utils import sha256_text
from app.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    AuditView,
    AuthResult,
    ClientLogBatchRequest,
    CallAppRequest,
    CallContext,
    DashboardSnapshot,
    DemoRunRequest,
    ExecutionTrace,
    ManualStateUpdateRequest,
    OpenClawToolCall,
    RequestTraceBundle,
    ResourceType,
    ScenarioDefinition,
    ScenarioPublicView,
    UnifiedLogEntry,
)


def _iso_from_epoch(value: Any) -> str | None:
    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pick_log_timestamp(payload: dict[str, Any], *, default: str | None = None) -> str:
    for key in ("timestamp", "issued_at", "decided_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    if "recorded_at" in payload:
        converted = _iso_from_epoch(payload.get("recorded_at"))
        if converted:
            return converted
    return default or _iso_now()


def _build_unified_log_entry(
    *,
    source: str,
    kind: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
    request_id: str | None = None,
    status: str | None = None,
    blocked_layer: str | None = None,
    block_number: int | None = None,
    timestamp: str | None = None,
    seq: int | None = None,
) -> UnifiedLogEntry:
    return UnifiedLogEntry(
        seq=seq,
        timestamp=timestamp or _pick_log_timestamp(payload),
        source=source,
        kind=kind,
        title=title,
        summary=summary,
        request_id=request_id,
        status=status,
        blocked_layer=blocked_layer,
        block_number=block_number,
        payload=payload,
    )


def _build_chain_log_from_request_trace(request_id: str, trace_bundle: dict[str, Any]) -> UnifiedLogEntry | None:
    traces = trace_bundle.get("traces") or []
    if not traces:
        return None
    last_trace = traces[-1] if isinstance(traces[-1], dict) else {}
    request = last_trace.get("request") or {}
    response = last_trace.get("response") or {}
    audit = response.get("audit") or {}
    chain_receipt = audit.get("chain_receipt")
    chain_block_number = audit.get("chain_block_number")
    if not chain_receipt and chain_block_number is None:
        return None
    payload = {
        "request_id": request_id,
        "digest": chain_receipt,
        "status": response.get("status") or trace_bundle.get("final_status") or "unknown",
        "blocked_layer": response.get("blocked_layer"),
        "app": request.get("app"),
        "action": request.get("action"),
        "block_number": chain_block_number,
        "recorded_at": trace_bundle.get("recorded_at"),
    }
    title = f'{payload.get("app", "chain")}.{payload.get("action", "audit")}'
    summary = f'{payload.get("status", "unknown")} / block {chain_block_number if chain_block_number is not None else "N/A"}'
    return _build_unified_log_entry(
        source="chain",
        kind="chain_audit",
        title=title,
        summary=summary,
        payload=payload,
        request_id=request_id,
        status=payload.get("status"),
        blocked_layer=payload.get("blocked_layer"),
        block_number=chain_block_number,
        timestamp=_pick_log_timestamp(payload, default=_iso_now()),
    )


def _build_dashboard_logs(container, limit: int = 30) -> list[UnifiedLogEntry]:
    logs: list[UnifiedLogEntry] = []

    for event in container.events.snapshot()[-limit:]:
        payload = dict(event.get("payload") or {})
        source = str(payload.get("source") or "realtime")
        summary = payload.get("line") or payload.get("message") or payload.get("summary") or event.get("kind", "")
        logs.append(
            _build_unified_log_entry(
                source=source,
                kind=str(event.get("kind", "event")),
                title=str(event.get("kind", "event")),
                summary=str(summary),
                payload=payload,
                request_id=payload.get("request_id"),
                status=payload.get("status") or payload.get("phase") or payload.get("final_status"),
                blocked_layer=payload.get("blocked_layer"),
                block_number=payload.get("block_number"),
                timestamp=str(event.get("timestamp") or _iso_now()),
                seq=int(event.get("seq", 0)) if event.get("seq") is not None else None,
            )
        )

    for entry in container.audit.recent(limit):
        payload = dict(entry)
        title = f'{payload.get("app", "audit")}.{payload.get("action", payload.get("event_type", "event"))}'
        summary = payload.get("message") or payload.get("reason") or payload.get("status") or title
        audit_log = _build_unified_log_entry(
            source="audit",
            kind=str(payload.get("event_type", "audit")),
            title=title,
            summary=str(summary),
            payload=payload,
            request_id=payload.get("request_id"),
            status=payload.get("status"),
            blocked_layer=payload.get("blocked_layer"),
            block_number=payload.get("block_number"),
        )
        logs.append(audit_log)

    approvals = container.approvals.list_pending()
    for approval in approvals[-limit:]:
        payload = approval.model_dump(mode="json")
        summary = f'{payload["target"]} · {payload["status"]}'
        logs.append(
            _build_unified_log_entry(
                source="approval",
                kind="approval",
                title=payload["status"],
                summary=summary,
                payload=payload,
                request_id=payload.get("request_id"),
                status=payload.get("status"),
                timestamp=_pick_log_timestamp(payload, default=_iso_now()),
            )
        )

    request_ids = [
        entry.request_id
        for entry in logs
        if entry.request_id
    ]
    request_trace_map = container.audit.request_traces(list(dict.fromkeys(request_ids)))
    for request_id, trace in request_trace_map.items():
        payload = dict(trace)
        summary = payload.get("summary") or payload.get("final_status") or "request trace"
        logs.append(
            _build_unified_log_entry(
                source="request_trace",
                kind="trace_bundle",
                title=str(payload.get("source", "trace")),
                summary=str(summary),
                payload=payload,
                request_id=request_id,
                status=payload.get("final_status"),
                blocked_layer=payload.get("blocked_layer"),
            )
        )
        chain_log = _build_chain_log_from_request_trace(request_id, trace)
        if chain_log is not None:
            logs.append(chain_log)

    def sort_key(item: UnifiedLogEntry) -> tuple[str, int, int, str]:
        return (
            item.timestamp,
            item.seq or 0,
            {"frontend": 5, "realtime": 4, "request_trace": 3, "audit": 2, "approval": 1, "chain": 0}.get(item.source, -1),
            item.title,
        )

    deduped: list[UnifiedLogEntry] = []
    seen: set[tuple[Any, ...]] = set()
    for item in sorted(logs, key=sort_key, reverse=True):
        key = (
            item.source,
            item.kind,
            item.request_id,
            item.status,
            item.blocked_layer,
            item.block_number,
            item.summary,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[: max(limit, 1) * 4]


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def public_scenario_payload(scenario: ScenarioDefinition) -> dict:
        return ScenarioPublicView(
            scenario_id=scenario.scenario_id,
            scenario_type=scenario.scenario_type,
            title=scenario.title,
            description=scenario.description,
            user_task=scenario.user_task,
        ).model_dump(mode="json")

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

    def record_frontend_log(container, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        enriched = {
            **payload,
            "source": "frontend",
            "user_agent": request.headers.get("user-agent"),
            "remote_addr": request.client.host if request.client else None,
        }
        audit_entry = container.audit.record({"event_type": "frontend_log", **enriched})
        container.events.publish(
            "frontend_log",
            {
                "source": "frontend",
                "kind": enriched.get("kind"),
                "level": enriched.get("level"),
                "message": enriched.get("message"),
                "page": enriched.get("page"),
                "url": enriched.get("url"),
                "request_id": enriched.get("request_id"),
                "context": enriched.get("context", {}),
                "audit": audit_entry,
            },
        )
        return audit_entry

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
                metadata={
                    **({"approval_token": payload.approval_token} if payload.approval_token else {}),
                    "debug_source": "demo_console_state_request",
                },
            ),
            context=CallContext(
                user_goal="手动更新受保护状态",
                trusted_system_goal=action,
                source_summary="demo console debug state editor",
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
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "scenarios": scenario_items,
                "scenario_payloads": [public_scenario_payload(scenario) for scenario in scenario_items],
                "state_targets": app_state.state_store.list_targets(),
                "identity": app_state.chain.demo_identity(),
                "approver_identity": app_state.chain.approver_identity(),
                "chain_status": app_state.chain.status_snapshot(),
                "openclaw_status": app_state.orchestrator.openclaw.status_snapshot(),
                "pending_approvals": [item.model_dump(mode="json") for item in app_state.approvals.list_pending()],
                "event_seq": app_state.events.latest_seq(),
            },
        )

    @router.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @router.get("/api/scenarios")
    async def scenarios(request: Request) -> dict:
        return {
            "items": [
                public_scenario_payload(scenario)
                for scenario in request.app.state.container.orchestrator.scenarios()
            ]
        }

    @router.post("/api/call_app")
    async def call_app(request: Request, payload: CallAppRequest) -> dict:
        container = request.app.state.container
        response = container.gateway.handle_call(payload)
        record_direct_trace(container, payload, response, source="direct_call")
        return response.model_dump(mode="json")

    @router.post("/api/client/logs")
    async def client_logs(request: Request, payload: ClientLogBatchRequest) -> dict:
        container = request.app.state.container
        stored = []
        for event in payload.events:
            stored.append(record_frontend_log(container, event.model_dump(mode="json"), request))
        return {"count": len(stored)}

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
        approvals = container.approvals.list_pending()
        if request_id:
            audit = [item for item in audit if item.get("request_id") == request_id]
            approvals = [item for item in approvals if item.request_id == request_id]
        request_ids = [item.get("request_id") for item in audit if item.get("request_id")]
        if request_id:
            request_ids = [request_id]
        request_trace_map = container.audit.request_traces(list(dict.fromkeys(request_ids)))
        chain = []
        for trace_request_id, trace in request_trace_map.items():
            chain_log = _build_chain_log_from_request_trace(trace_request_id, trace)
            if chain_log is None:
                continue
            chain.append(
                {
                    "request_id": chain_log.request_id,
                    "digest": chain_log.payload.get("digest"),
                    "status": chain_log.status,
                    "blocked_layer": chain_log.blocked_layer,
                    "app": chain_log.payload.get("app"),
                    "action": chain_log.payload.get("action"),
                    "block_number": chain_log.block_number,
                    "recorded_at": chain_log.timestamp,
                }
            )
        timeline = _build_dashboard_logs(container, limit=30)
        if request_id:
            timeline = [item for item in timeline if item.request_id == request_id]
        return {
            "audit": audit,
            "chain": chain,
            "approvals": [item.model_dump(mode="json") for item in approvals],
            "request_trace": container.audit.request_trace(request_id) if request_id else None,
            "timeline": [item.model_dump(mode="json") for item in timeline],
        }

    @router.get("/api/chain/status")
    async def chain_status(request: Request) -> dict:
        container = request.app.state.container
        return container.chain.status_view(force_refresh=True).model_dump(mode="json")

    @router.get("/api/dashboard")
    async def dashboard(request: Request, limit: int = 30) -> dict:
        container = request.app.state.container
        snapshot = DashboardSnapshot(
            openclaw_status=asdict(container.orchestrator.openclaw.status_snapshot()),
            chain_status=container.chain.status_snapshot().model_dump(mode="json"),
            state={
                "targets": container.state_store.list_targets(),
                "baselines": container.integrity.baselines(),
                "drift": container.integrity.drift_report(),
            },
            pending_approvals=[item.model_dump(mode="json") for item in container.approvals.list_pending()],
            logs=[item.model_dump(mode="json") for item in _build_dashboard_logs(container, limit=max(5, limit))],
            event_seq=container.events.latest_seq(),
        )
        return snapshot.model_dump(mode="json")

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

    @router.get("/api/events/stream")
    async def event_stream(request: Request) -> StreamingResponse:
        container = request.app.state.container

        async def generator():
            last_seq = 0
            since = request.query_params.get("since")
            if since:
                try:
                    last_seq = int(since)
                except ValueError:
                    last_seq = 0
            while True:
                events = container.events.since(last_seq)
                if events:
                    for event in events:
                        last_seq = int(event["seq"])
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/state")
    async def state_snapshot(request: Request) -> dict:
        container = request.app.state.container
        return {
            "targets": container.state_store.list_targets(),
            "baselines": container.integrity.baselines(),
            "drift": container.integrity.drift_report(),
        }

    return router

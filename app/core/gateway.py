from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.apps.services import BankApp, GalleryApp, MailApp, ProtectedStateStore, WeatherApp
from app.audit.service import AuditService
from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
from app.core.actions import get_action_spec, render_state_update_content
from app.core.request_builder import signed_payload_from_request
from app.core.realtime import RealtimeEventJournal
from app.guards.intent import IntentGuard
from app.schemas import AppCallResult, AuditView, AuthResult, CallAppRequest, CallAppResponse, Mode, ResourceType
from app.state.store import StateIntegrityService


@dataclass
class SandboxDispatcher:
    mail: MailApp
    bank: BankApp
    gallery: GalleryApp
    weather: WeatherApp
    state_store: ProtectedStateStore
    integrity: StateIntegrityService

    def __post_init__(self) -> None:
        self._executors = {
            "mail.list_messages": lambda request: self.mail.list_messages(),
            "mail.read_message": lambda request: self.mail.read_message(request.args["message_id"]),
            "bank.get_balance": lambda request: self.bank.get_balance(request.args.get("account", "demo-user")),
            "bank.transfer": lambda request: self.bank.transfer(
                target_account=request.args["target_account"],
                amount=float(request.args["amount"]),
                memo=request.args.get("memo", ""),
                transfer_limit=float(self.state_store.system_config().get("transfer_limit", 2000)),
            ),
            "gallery.list_assets": lambda request: self.gallery.list_assets(),
            "gallery.read_asset": lambda request: self.gallery.read_asset(request.args["asset_id"]),
            "weather.get_weather": lambda request: self.weather.get_weather(request.args["city"]),
            "weather.get_alert": lambda request: self.weather.get_alert(request.args["city"]),
        }

    def execute(self, request: CallAppRequest) -> tuple[AppCallResult, Any | None]:
        request = self._validated_request(request)
        if request.resource_type == ResourceType.APP:
            return self._execute_app(request), None
        return self._execute_state(request)

    def reset_runtime(self) -> None:
        for service in (self.mail, self.bank, self.gallery, self.weather):
            reset = getattr(service, "reset", None)
            if callable(reset):
                reset()

    def _validated_request(self, request: CallAppRequest) -> CallAppRequest:
        spec = get_action_spec(request.app, request.action)
        args = spec.validate_request(request)
        return request.model_copy(update={"args": args})

    def _execute_app(self, request: CallAppRequest) -> AppCallResult:
        permission_key = self.permission_key(request)
        executor = self._executors.get(permission_key)
        if executor is None:
            raise ValueError(f"unsupported app action: {permission_key}")
        return executor(request)

    def _execute_state(self, request: CallAppRequest) -> tuple[AppCallResult, Any]:
        target = request.args["target"]
        current_content = self.state_store.read(target)
        new_content = render_state_update_content(target, current_content, request.args)
        diff = self.integrity.inspect_change(
            target,
            new_content,
            request_id=request.request_id,
            payload_hash=request.payload_hash,
            approval_token=str(request.metadata.get("approval_token", "")) or None,
        )
        if request.mode == Mode.OFF:
            self.state_store.write(target, new_content)
            diff.applied = True
            return (
                AppCallResult(summary=f"off 模式直接更新状态 {target}", data={"target": target, "applied": True}),
                diff,
            )
        if diff.suspicious and not diff.approval_granted:
            diff = self.integrity.rollback(diff)
            return (
                AppCallResult(summary="受保护状态修改被拦截", data={"target": target, "applied": False}),
                diff,
            )
        diff = self.integrity.apply(diff, new_content)
        return (
            AppCallResult(summary=f"已更新受保护状态 {target}", data={"target": target, "applied": True}),
            diff,
        )

    @staticmethod
    def permission_key(request: CallAppRequest) -> str:
        return get_action_spec(request.app, request.action).permission_key


@dataclass
class GatewayService:
    signer: RequestSigner
    guard: IntentGuard
    chain: FiscoBcosService
    dispatcher: SandboxDispatcher
    audit: AuditService
    events: RealtimeEventJournal | None = None

    def _publish_stage(
        self,
        request: CallAppRequest,
        stage: str,
        summary: str,
        *,
        status: str | None = None,
        blocked_layer: str | None = None,
        line: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.events is None:
            return
        self.events.publish(
            "gateway_trace",
            {
                "session_id": request.session_id,
                "request_id": request.request_id,
                "app": request.app,
                "action": request.action,
                "stage": stage,
                "summary": summary,
                "status": status,
                "blocked_layer": blocked_layer,
                "line": line or f"[{request.request_id}] {stage} {summary}",
                **(payload or {}),
            },
        )

    def handle_call(self, request: CallAppRequest) -> CallAppResponse:
        started = time.perf_counter()
        guard_decision = None
        auth_result = None
        blocked_layer = None
        status = "executed"
        message = "request executed"
        result = None
        state_change = None
        permission_key = None
        spec = None

        try:
            spec = get_action_spec(request.app, request.action)
            permission_key = spec.permission_key
            self._publish_stage(
                request,
                "request_received",
                "统一网关已接收调用请求",
                status="received",
                line=f"[gateway] recv {request.request_id} {request.app}.{request.action}",
                payload={
                    "mode": request.mode.value,
                    "resource_type": request.resource_type.value,
                    "permission_key": permission_key,
                    "args": request.args,
                },
            )
            signed_payload = signed_payload_from_request(request)
            expected_hash = self.signer.payload_hash(signed_payload)
            if request.payload_hash != expected_hash:
                self._publish_stage(
                    request,
                    "request_rejected",
                    "payload hash mismatch",
                    status="error",
                    blocked_layer="system",
                    line=f"[gateway] reject {request.request_id} payload hash mismatch",
                )
                raise ValueError("payload hash mismatch")
            request = self.dispatcher._validated_request(request)
            self._publish_stage(
                request,
                "request_validated",
                "请求签名与参数校验通过",
                status="ok",
                line=f"[gateway] validated {request.app}.{request.action}",
                payload={"args": request.args},
            )
            config = self.dispatcher.state_store.system_config()
            if "guard_threshold_override" in config:
                self.guard.threshold = float(config["guard_threshold_override"])

            if request.mode != Mode.OFF:
                self._publish_stage(
                    request,
                    "guard_started",
                    "开始执行意图护栏",
                    status="running",
                    line=f"[guard] evaluating {request.app}.{request.action}",
                )
                guard_decision = self.guard.evaluate(request)
                self._publish_stage(
                    request,
                    "guard_result",
                    guard_decision.reason,
                    status="blocked" if not guard_decision.allowed else "passed",
                    blocked_layer="guard" if not guard_decision.allowed else None,
                    line=(
                        f"[guard] {'block' if not guard_decision.allowed else 'pass'} "
                        f"stage={guard_decision.decision_stage} sim={guard_decision.intent_similarity:.3f}"
                    ),
                    payload={"guard": guard_decision.model_dump(mode="json")},
                )
                if not guard_decision.allowed:
                    blocked_layer = "guard"
                    status = "blocked"
                    message = guard_decision.reason
                    return self._build_response(
                        request,
                        started,
                        status=status,
                        blocked_layer=blocked_layer,
                        message=message,
                        result=None,
                        guard=guard_decision,
                        auth=None,
                        state_change=None,
                    )

            if request.mode == Mode.FULL:
                self._publish_stage(
                    request,
                    "chain_started",
                    "开始链上验签与验权",
                    status="running",
                    line=f"[chain] authorize {permission_key}",
                )
                auth = self.chain.authorize(signed_payload, request.signature, request.did, permission_key)
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
                if not auth.chain_available and spec and not spec.fail_closed_on_chain_error:
                    auth_result.degraded_allowed = True
                    auth_result.reason = "chain unavailable, degraded allow for read-only action"
                    self._publish_stage(
                        request,
                        "chain_result",
                        auth_result.reason,
                        status="degraded",
                        line=f"[chain] degraded allow {permission_key}",
                        payload={"auth": auth_result.model_dump(mode="json")},
                    )
                elif not auth.verified or not auth.permission_allowed:
                    self._publish_stage(
                        request,
                        "chain_result",
                        auth.reason,
                        status="blocked",
                        blocked_layer="chain",
                        line=f"[chain] block {permission_key}: {auth.reason}",
                        payload={"auth": auth_result.model_dump(mode="json")},
                    )
                    blocked_layer = "chain"
                    status = "blocked"
                    message = auth.reason
                    return self._build_response(
                        request,
                        started,
                        status=status,
                        blocked_layer=blocked_layer,
                        message=message,
                        result=None,
                        guard=guard_decision,
                        auth=auth_result,
                        state_change=None,
                    )
                else:
                    self._publish_stage(
                        request,
                        "chain_result",
                        auth.reason,
                        status="passed",
                        line=f"[chain] pass {permission_key}",
                        payload={"auth": auth_result.model_dump(mode="json")},
                    )

            self._publish_stage(
                request,
                "dispatch_started",
                "开始执行工具调用",
                status="running",
                line=f"[dispatch] execute {request.app}.{request.action}",
            )
            result, state_change = self.dispatcher.execute(request)
            if state_change and not state_change.applied:
                blocked_layer = "state"
                status = "blocked"
                message = (
                    "protected state update was rejected"
                    if state_change.approval_status == "rejected"
                    else "protected state update requires approval"
                )
                self._publish_stage(
                    request,
                    "state_result",
                    message,
                    status="blocked",
                    blocked_layer="state",
                    line=(
                        f"[state] block {state_change.target} "
                        f"status={state_change.approval_status or 'blocked'}"
                    ),
                    payload={"state_change": state_change.model_dump(mode="json")},
                )
            elif state_change:
                message = "protected state updated"
                self._publish_stage(
                    request,
                    "state_result",
                    message,
                    status="applied",
                    line=f"[state] applied {state_change.target}",
                    payload={"state_change": state_change.model_dump(mode="json")},
                )
            elif auth_result and auth_result.degraded_allowed:
                message = "request executed under degraded chain policy"
                self._publish_stage(
                    request,
                    "dispatch_result",
                    message,
                    status="executed",
                    line=f"[dispatch] executed degraded {request.app}.{request.action}",
                    payload={"result": result.model_dump(mode="json") if result else None},
                )
            else:
                self._publish_stage(
                    request,
                    "dispatch_result",
                    message,
                    status="executed",
                    line=f"[dispatch] executed {request.app}.{request.action}",
                    payload={"result": result.model_dump(mode="json") if result else None},
                )

            return self._build_response(
                request,
                started,
                status=status,
                blocked_layer=blocked_layer,
                message=message,
                result=result,
                guard=guard_decision,
                auth=auth_result,
                state_change=state_change,
                permission_key=permission_key,
                risk_level=spec.risk_level if spec else None,
                approval_required=bool(spec.approval_required if spec else False),
            )
        except Exception as exc:
            return self._build_response(
                request,
                started,
                status="error",
                blocked_layer="system",
                message=str(exc),
                result=None,
                guard=guard_decision,
                auth=auth_result,
                state_change=state_change,
                permission_key=permission_key,
                risk_level=spec.risk_level if spec else None,
                approval_required=bool(spec.approval_required if spec else False),
            )

    def _build_response(
        self,
        request: CallAppRequest,
        started: float,
        *,
        status: str,
        blocked_layer: str | None,
        message: str,
        result: AppCallResult | None,
        guard: Any,
        auth: Any,
        state_change: Any,
        permission_key: str | None = None,
        risk_level: str | None = None,
        approval_required: bool = False,
    ) -> CallAppResponse:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        audit_entry = self.audit.record(
            {
                "request_id": request.request_id,
                "session_id": request.session_id,
                "did": request.did,
                "resource_type": request.resource_type.value,
                "app": request.app,
                "action": request.action,
                "mode": request.mode.value,
                "status": status,
                "blocked_layer": blocked_layer,
                "message": message,
                "user_goal": request.context.user_goal,
                "permission_key": permission_key,
                "risk_level": risk_level,
                "approval_required": approval_required,
                "guard": guard.model_dump() if guard else None,
                "auth": auth.model_dump() if auth else None,
                "result": result.model_dump() if result else None,
                "state_change": state_change.model_dump(exclude={"approval_token"}) if state_change else None,
                "latency_ms": latency_ms,
            }
        )
        if self.events is not None:
            self.events.publish(
                "audit",
                {
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "status": status,
                    "blocked_layer": blocked_layer,
                    "message": message,
                    "permission_key": permission_key,
                    "risk_level": risk_level,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "audit": audit_entry,
                },
            )
            self.events.publish(
                "gateway_trace",
                {
                    "session_id": request.session_id,
                    "request_id": request.request_id,
                    "app": request.app,
                    "action": request.action,
                    "stage": "audit_result",
                    "summary": "审计日志已写入",
                    "status": status,
                    "blocked_layer": blocked_layer,
                    "line": f"[audit] {request.request_id} {status} {message}",
                    "audit": audit_entry,
                },
            )
        chain_receipt = None
        chain_backend = None
        chain_block_number = None
        if request.mode == Mode.FULL:
            try:
                chain_receipt, chain_block_number, chain_backend = self.chain.record_audit(
                    request.request_id,
                    {
                        "status": status,
                        "blocked_layer": blocked_layer,
                        "app": request.app,
                        "action": request.action,
                        "message": message,
                    },
                )
            except Exception as exc:
                chain_backend = f"audit_unavailable:{type(exc).__name__}"
        return CallAppResponse(
            request_id=request.request_id,
            mode=request.mode,
            allowed=status == "executed",
            status=status,
            blocked_layer=blocked_layer,
            message=message,
            result=result,
            guard=guard,
            auth=auth,
            audit=AuditView(
                request_id=request.request_id,
                entry_hash=audit_entry["entry_hash"],
                previous_hash=audit_entry["previous_hash"],
                chain_receipt=chain_receipt,
                chain_backend=chain_backend,
                chain_block_number=chain_block_number,
            ),
            state_change=state_change,
            latency_ms=latency_ms,
            permission_key=permission_key,
            risk_level=risk_level,
            approval_required=approval_required,
        )

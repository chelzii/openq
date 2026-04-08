from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.apps.services import BankApp, GalleryApp, MailApp, ProtectedStateStore, WeatherApp
from app.audit.service import AuditService
from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
from app.core.actions import get_action_spec
from app.core.request_builder import signed_payload_from_request
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
        new_content = request.args["content"]
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
            signed_payload = signed_payload_from_request(request)
            expected_hash = self.signer.payload_hash(signed_payload)
            if request.payload_hash != expected_hash:
                raise ValueError("payload hash mismatch")
            request = self.dispatcher._validated_request(request)
            config = self.dispatcher.state_store.system_config()
            if "guard_threshold_override" in config:
                self.guard.threshold = float(config["guard_threshold_override"])

            if request.mode != Mode.OFF:
                guard_decision = self.guard.evaluate(request)
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
                elif not auth.verified or not auth.permission_allowed:
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

            result, state_change = self.dispatcher.execute(request)
            if state_change and not state_change.applied:
                blocked_layer = "state"
                status = "blocked"
                message = (
                    "protected state update was rejected"
                    if state_change.approval_status == "rejected"
                    else "protected state update requires approval"
                )
            elif state_change:
                message = "protected state updated"
            elif auth_result and auth_result.degraded_allowed:
                message = "request executed under degraded chain policy"

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

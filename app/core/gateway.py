from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.apps.services import BankApp, GalleryApp, MailApp, ProtectedStateStore, WeatherApp
from app.audit.service import AuditService
from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
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

    def execute(self, request: CallAppRequest, approval_token: str | None = None) -> tuple[AppCallResult, Any | None]:
        if request.resource_type == ResourceType.APP:
            return self._execute_app(request), None
        return self._execute_state(request, approval_token)

    def _execute_app(self, request: CallAppRequest) -> AppCallResult:
        config = self.state_store.system_config()
        if request.app == "mail":
            if request.action == "list_messages":
                return self.mail.list_messages()
            if request.action == "read_message":
                return self.mail.read_message(request.args["message_id"])
        if request.app == "bank":
            if request.action == "get_balance":
                return self.bank.get_balance(request.args.get("account", "demo-user"))
            if request.action == "transfer":
                return self.bank.transfer(
                    target_account=request.args["target_account"],
                    amount=float(request.args["amount"]),
                    memo=request.args.get("memo", ""),
                    transfer_limit=float(config.get("transfer_limit", 2000)),
                )
        if request.app == "gallery":
            if request.action == "list_assets":
                return self.gallery.list_assets()
            if request.action == "read_asset":
                return self.gallery.read_asset(request.args["asset_id"])
        if request.app == "weather":
            if request.action == "get_weather":
                return self.weather.get_weather(request.args["city"])
            if request.action == "get_alert":
                return self.weather.get_alert(request.args["city"])
        raise ValueError(f"unsupported app action: {request.app}.{request.action}")

    def _execute_state(self, request: CallAppRequest, approval_token: str | None) -> tuple[AppCallResult, Any]:
        target = request.args["target"]
        new_content = request.args["content"]
        diff = self.integrity.inspect_change(target, new_content, approval_token)
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
        return f"{request.app}.{request.action}"


@dataclass
class GatewayService:
    signer: RequestSigner
    guard: IntentGuard
    chain: FiscoBcosService
    dispatcher: SandboxDispatcher
    audit: AuditService

    def handle_call(self, request: CallAppRequest, approval_token: str | None = None) -> CallAppResponse:
        started = time.perf_counter()
        guard_decision = None
        auth_result = None
        blocked_layer = None
        status = "executed"
        message = "request executed"
        result = None
        state_change = None
        permission_key = self.dispatcher.permission_key(request)

        try:
            signed_payload = request.model_dump(exclude={"signature", "payload_hash"})
            expected_hash = self.signer.payload_hash(signed_payload)
            if request.payload_hash != expected_hash:
                raise ValueError("payload hash mismatch")
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
                )
                if not auth.verified or not auth.permission_allowed:
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

            result, state_change = self.dispatcher.execute(request, approval_token)
            if state_change and not state_change.applied:
                blocked_layer = "state"
                status = "blocked"
                message = "protected state update requires approval"
            elif state_change:
                message = "protected state updated"

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
                "guard": guard.model_dump() if guard else None,
                "auth": auth.model_dump() if auth else None,
                "result": result.model_dump() if result else None,
                "state_change": state_change.model_dump() if state_change else None,
                "latency_ms": latency_ms,
            }
        )
        chain_receipt = None
        chain_backend = None
        chain_block_number = None
        if request.mode == Mode.FULL:
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
        )

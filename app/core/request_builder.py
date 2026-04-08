from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
from app.core.actions import get_action_spec
from app.schemas import CallAppRequest, CallContext, Mode, OpenClawToolCall

TRANSIENT_METADATA_KEYS = {"approval_token"}


def normalized_request_metadata(metadata: dict[str, object] | None) -> dict[str, object]:
    payload = metadata or {}
    return {key: value for key, value in payload.items() if key not in TRANSIENT_METADATA_KEYS and value is not None}


def signed_payload_from_request(request: CallAppRequest) -> dict[str, object]:
    payload = request.model_dump(exclude={"signature", "payload_hash"})
    payload["metadata"] = normalized_request_metadata(request.metadata)
    return payload


@dataclass
class SignedCallBuilder:
    signer: RequestSigner
    chain: FiscoBcosService

    def build(
        self,
        *,
        session_id: str,
        mode: Mode,
        intent: OpenClawToolCall,
        context: CallContext,
        request_id: str | None = None,
        identity_key: str = "agent",
    ) -> CallAppRequest:
        identity = self.chain.identity(identity_key)
        spec = get_action_spec(intent.app, intent.action)
        normalized_intent = intent.model_copy(update={"args": spec.validate_args(intent.args)})
        payload = {
            "request_id": request_id or f"{intent.app}-{intent.action}-{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "mode": mode.value,
            "did": identity.did,
            "resource_type": normalized_intent.resource_type.value,
            "app": normalized_intent.app,
            "action": normalized_intent.action,
            "args": normalized_intent.args,
            "context": context.model_dump(mode="json"),
            "metadata": normalized_request_metadata(normalized_intent.metadata),
        }
        payload_hash = self.signer.payload_hash(payload)
        signature = self.signer.sign_payload(payload, identity.private_key)
        return CallAppRequest.model_validate({**payload, "payload_hash": payload_hash, "signature": signature})

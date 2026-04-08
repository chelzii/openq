from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import time

from app.core.utils import read_json, write_json
from app.schemas import ApprovalView


@dataclass
class ApprovalService:
    approval_store: Path

    def _read(self) -> dict[str, dict[str, str | list[str] | None]]:
        return read_json(self.approval_store, {})

    def _write(self, payload: dict[str, dict[str, str | list[str] | None]]) -> None:
        write_json(self.approval_store, payload)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def issue(
        self,
        *,
        request_id: str,
        payload_hash: str,
        target: str,
        diff: str,
        risk_labels: list[str],
    ) -> ApprovalView:
        approvals = self._read()
        for token, item in approvals.items():
            if item.get("request_id") == request_id and item.get("payload_hash") == payload_hash and item.get("status") in {"pending", "approved", "rejected"}:
                return ApprovalView.model_validate({"token": token, **item})

        token = secrets.token_urlsafe(24)
        record: dict[str, str | list[str] | None] = {
            "request_id": request_id,
            "payload_hash": payload_hash,
            "target": target,
            "diff": diff,
            "risk_labels": risk_labels,
            "status": "pending",
            "issued_at": self._now(),
            "decided_at": None,
            "decision_by": None,
            "decision_note": None,
        }
        approvals[token] = record
        self._write(approvals)
        return ApprovalView.model_validate({"token": token, **record})

    def get(self, token: str) -> ApprovalView | None:
        approvals = self._read()
        payload = approvals.get(token)
        if not payload:
            return None
        return ApprovalView.model_validate({"token": token, **payload})

    def list_pending(self) -> list[ApprovalView]:
        approvals = self._read()
        pending = []
        for token, payload in approvals.items():
            if payload.get("status") == "pending":
                pending.append(ApprovalView.model_validate({"token": token, **payload}))
        return sorted(pending, key=lambda item: item.issued_at, reverse=True)

    def decide(self, token: str, decision: str, approver_did: str, note: str = "") -> ApprovalView:
        approvals = self._read()
        payload = approvals.get(token)
        if not payload:
            raise ValueError("approval token not found")
        if payload.get("status") != "pending":
            raise ValueError("approval token is no longer actionable")
        payload["status"] = "approved" if decision == "approve" else "rejected"
        payload["decided_at"] = self._now()
        payload["decision_by"] = approver_did
        payload["decision_note"] = note
        approvals[token] = payload
        self._write(approvals)
        return ApprovalView.model_validate({"token": token, **payload})

    def consume(self, token: str | None, request_id: str, payload_hash: str) -> ApprovalView | None:
        if not token:
            return None
        approvals = self._read()
        payload = approvals.get(token)
        if not payload:
            return None
        if payload.get("request_id") != request_id or payload.get("payload_hash") != payload_hash:
            return None
        if payload.get("status") != "approved":
            return None
        payload["status"] = "consumed"
        approvals[token] = payload
        self._write(approvals)
        return ApprovalView.model_validate({"token": token, **payload})

    def reset(self) -> None:
        self._write({})

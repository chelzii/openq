from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import time

from app.core.utils import read_json, write_json


@dataclass
class ApprovalService:
    approval_store: Path

    def _read(self) -> dict[str, dict[str, str]]:
        return read_json(self.approval_store, {})

    def _write(self, payload: dict[str, dict[str, str]]) -> None:
        write_json(self.approval_store, payload)

    def issue(self, request_id: str, payload_hash: str) -> str:
        token = secrets.token_urlsafe(24)
        approvals = self._read()
        approvals[token] = {
            "request_id": request_id,
            "payload_hash": payload_hash,
            "issued_at": str(int(time.time())),
            "consumed": "false",
        }
        self._write(approvals)
        return token

    def consume(self, token: str | None, request_id: str, payload_hash: str) -> bool:
        if not token:
            return False
        approvals = self._read()
        payload = approvals.get(token)
        if not payload:
            return False
        if payload.get("consumed") == "true":
            return False
        if payload.get("request_id") != request_id or payload.get("payload_hash") != payload_hash:
            return False
        payload["consumed"] = "true"
        approvals[token] = payload
        self._write(approvals)
        return True

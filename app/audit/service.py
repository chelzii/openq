from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.utils import append_jsonl, read_json, sha256_json, write_json


class AuditService:
    def __init__(self, log_path: Path, trace_store_path: Path | None = None):
        self.log_path = log_path
        self.trace_store_path = trace_store_path or log_path.with_name("request_traces.json")

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        entries = self._read_entries()
        previous_hash = entries[-1]["entry_hash"] if entries else "0" * 64
        stored = {
            **payload,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "previous_hash": previous_hash,
        }
        stored["entry_hash"] = sha256_json(stored)
        append_jsonl(self.log_path, stored)
        return stored

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        entries = self._read_entries()
        return entries[-limit:]

    def entries_for_requests(self, request_ids: list[str]) -> list[dict[str, Any]]:
        if not request_ids:
            return []
        wanted = set(request_ids)
        return [entry for entry in self._read_entries() if entry.get("request_id") in wanted]

    def export_snapshot(self, path: Path) -> None:
        write_json(path, self.recent(500))

    def record_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.record({"event_type": "approval", **payload})

    def _read_trace_store(self) -> dict[str, dict[str, Any]]:
        payload = read_json(self.trace_store_path, {})
        return payload if isinstance(payload, dict) else {}

    def record_request_trace(
        self,
        payload: dict[str, Any],
        *,
        request_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        trace_store = self._read_trace_store()
        stored = {
            **payload,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        for request_id in request_ids or [payload["request_id"]]:
            trace_store[request_id] = stored
        write_json(self.trace_store_path, trace_store)
        return stored

    def request_trace(self, request_id: str) -> dict[str, Any] | None:
        trace_store = self._read_trace_store()
        return trace_store.get(request_id)

    def request_traces(self, request_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not request_ids:
            return {}
        trace_store = self._read_trace_store()
        wanted = set(request_ids)
        return {request_id: payload for request_id, payload in trace_store.items() if request_id in wanted}

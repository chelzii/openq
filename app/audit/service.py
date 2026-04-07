from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.core.utils import append_jsonl, sha256_json, write_json


class AuditService:
    def __init__(self, log_path: Path):
        self.log_path = log_path

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

    def export_snapshot(self, path: Path) -> None:
        write_json(path, self.recent(500))

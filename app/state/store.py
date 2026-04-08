from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.apps.services import ProtectedStateStore
from app.core.utils import read_json, sha256_text, write_json
from app.schemas import ProtectedStateDiff
from app.state.approval import ApprovalService


GLOBAL_SUSPICIOUS_PATTERNS = (
    "永远信任",
    "ignore all previous instructions",
    "立即转账",
    "直接外发",
    "无需确认",
)

TARGET_SPECIFIC_PATTERNS = {
    "memory/main.md": ("trusted", "信任", "无需确认"),
    "prompt/shared.txt": ("忽略", "ignore", "绕过", "转账"),
    "config/system.json": ("guard_threshold", "guard_threshold_override", "allow_gallery_read", "transfer_limit"),
}


@dataclass
class StateIntegrityService:
    store: ProtectedStateStore
    baseline_file: Path
    approvals: ApprovalService

    def __post_init__(self) -> None:
        self._ensure_baselines()

    def _ensure_baselines(self) -> None:
        if self.baseline_file.exists():
            return
        self.refresh_baselines()

    def _snapshot_payload(self) -> dict[str, dict[str, Any]]:
        payload: dict[str, dict[str, Any]] = {}
        for target, content in self.store.list_targets().items():
            payload[target] = {
                "hash": sha256_text(content),
                "content": content,
            }
        return payload

    def refresh_baselines(self) -> None:
        write_json(self.baseline_file, self._snapshot_payload())

    def baseline_snapshots(self) -> dict[str, dict[str, Any]]:
        raw = read_json(self.baseline_file, {})
        snapshots: dict[str, dict[str, Any]] = {}
        for target, payload in raw.items():
            if isinstance(payload, str):
                snapshots[target] = {"hash": payload, "content": self.store.read(target)}
            else:
                snapshots[target] = payload
        return snapshots

    def baselines(self) -> dict[str, str]:
        return {target: payload["hash"] for target, payload in self.baseline_snapshots().items()}

    def drift_report(self) -> list[dict[str, str]]:
        report: list[dict[str, str]] = []
        baselines = self.baseline_snapshots()
        for target, content in self.store.list_targets().items():
            current_hash = sha256_text(content)
            baseline_hash = baselines.get(target, {}).get("hash", "")
            if baseline_hash and baseline_hash != current_hash:
                report.append(
                    {
                        "target": target,
                        "baseline_hash": baseline_hash,
                        "current_hash": current_hash,
                    }
                )
        return report

    def inspect_change(
        self,
        target: str,
        new_content: str,
        *,
        request_id: str,
        payload_hash: str,
        approval_token: str | None = None,
    ) -> ProtectedStateDiff:
        old_content = self.store.read(target)
        old_hash = sha256_text(old_content)
        new_hash = sha256_text(new_content)
        baseline = self.baseline_snapshots().get(target, {"hash": old_hash, "content": old_content})
        baseline_hash = baseline["hash"]
        diff = "\n".join(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"{target} (old)",
                tofile=f"{target} (new)",
                lineterm="",
            )
        )
        lowered = new_content.lower()
        risk_labels: list[str] = []
        if any(pattern.lower() in lowered for pattern in GLOBAL_SUSPICIOUS_PATTERNS):
            risk_labels.append("policy_injection")
        if any(pattern.lower() in lowered for pattern in TARGET_SPECIFIC_PATTERNS.get(target, ())):
            risk_labels.append("target_sensitive_change")
        baseline_drift_detected = baseline_hash != old_hash
        if baseline_drift_detected:
            risk_labels.append("baseline_drift")
        suspicious = bool(risk_labels)
        diff_result = ProtectedStateDiff(
            target=target,
            old_hash=old_hash,
            baseline_hash=baseline_hash,
            new_hash=new_hash,
            diff=diff,
            suspicious=suspicious,
            approval_required=suspicious,
            approval_granted=False,
            baseline_drift_detected=baseline_drift_detected,
            risk_labels=risk_labels,
        )
        if not suspicious:
            return diff_result

        approved = self.approvals.consume(approval_token, request_id, payload_hash)
        if approved is not None:
            return self._bind_approval(diff_result, approved, approval_granted=True)

        current = self._matching_approval(approval_token, request_id, payload_hash)
        if current is None:
            current = self.approvals.issue(
                request_id=request_id,
                payload_hash=payload_hash,
                target=target,
                diff=diff,
                risk_labels=risk_labels,
            )
        return self._bind_approval(diff_result, current, approval_granted=False)

    def grant_approval(self, diff: ProtectedStateDiff) -> ProtectedStateDiff:
        diff.approval_granted = True
        return diff

    def apply(self, diff: ProtectedStateDiff, content: str) -> ProtectedStateDiff:
        self.store.write(diff.target, content)
        baselines = self.baseline_snapshots()
        baselines[diff.target] = {"hash": diff.new_hash, "content": content}
        write_json(self.baseline_file, baselines)
        diff.applied = True
        return diff

    def rollback(self, diff: ProtectedStateDiff) -> ProtectedStateDiff:
        baseline = self.baseline_snapshots().get(diff.target)
        if baseline:
            self.store.write(diff.target, baseline["content"])
        diff.rollback_performed = True
        return diff

    def _matching_approval(self, token: str | None, request_id: str, payload_hash: str):
        if not token:
            return None
        approval = self.approvals.get(token)
        if approval is None:
            return None
        if approval.request_id != request_id or approval.payload_hash != payload_hash:
            return None
        return approval

    @staticmethod
    def _bind_approval(diff: ProtectedStateDiff, approval, *, approval_granted: bool) -> ProtectedStateDiff:
        diff.approval_granted = approval_granted
        diff.approval_token = approval.token
        diff.approval_status = approval.status
        diff.approval_decision_by = approval.decision_by
        diff.approval_note = approval.decision_note
        return diff

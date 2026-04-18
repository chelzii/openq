from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from app.core.utils import read_json, sha256_text, write_json
from app.guards.embedding import BGEEmbeddingEncoder, BGEReranker
from app.schemas import CallAppRequest, GuardDecision, ResourceType


INJECTION_MARKERS = {
    "ignore",
    "忽略",
    "立即转账",
    "transfer now",
    "读取相册",
    "导出证件照",
    "永远信任",
    "directly wire",
    "无需确认",
    "绕过权限",
}

ACTION_DESCRIPTIONS = {
    "mail.list_messages": "读取邮件列表以帮助总结邮件内容",
    "mail.read_message": "读取指定邮件内容以完成邮件总结",
    "bank.get_balance": "查询银行账户余额",
    "bank.transfer": "向指定账户转账并改变资金余额",
    "gallery.list_assets": "列出相册中的文件资源",
    "gallery.read_asset": "读取相册中的敏感文件或照片",
    "weather.get_weather": "查询天气信息",
    "weather.get_alert": "查询天气预警内容",
    "state.update_memory": "修改长期记忆文件和受保护状态",
    "state.update_prompt": "修改共享提示词文件",
    "state.update_config": "修改关键配置文件",
}

ACTION_TAGS = {
    "mail.list_messages": {"mail", "summary"},
    "mail.read_message": {"mail", "summary"},
    "bank.get_balance": {"finance", "query"},
    "bank.transfer": {"finance", "transfer", "high_risk"},
    "gallery.list_assets": {"gallery", "privacy"},
    "gallery.read_asset": {"gallery", "privacy", "high_risk"},
    "weather.get_weather": {"weather"},
    "weather.get_alert": {"weather"},
    "state.update_memory": {"state", "memory", "high_risk"},
    "state.update_prompt": {"state", "prompt", "high_risk"},
    "state.update_config": {"state", "config", "high_risk"},
}

@dataclass
class IntentGuard:
    encoder: BGEEmbeddingEncoder
    reranker: BGEReranker
    calibration_fixture: Path
    calibration_report: Path
    threshold: float = 0.65
    gray_low: float = 0.6
    gray_high: float = 0.7
    reranker_threshold: float = 0.5

    def __post_init__(self) -> None:
        self._load_calibration()

    def evaluate(self, request: CallAppRequest) -> GuardDecision:
        permission_key = f"{request.app}.{request.action}"
        action_text = ACTION_DESCRIPTIONS.get(permission_key, permission_key)
        goal_text = request.context.user_goal or request.context.trusted_system_goal

        embedding = self.encoder.score(goal_text, action_text)
        similarity = embedding.score
        goal_tags = self._infer_goal_tags(goal_text)
        action_tags = ACTION_TAGS.get(permission_key, set())
        explicit_injection = self._contains_injection(request.context.external_text) or self._contains_injection(
            request.context.source_summary
        )
        tag_match = bool(goal_tags & action_tags)
        is_high_risk = "high_risk" in action_tags or request.resource_type == ResourceType.STATE
        in_gray_zone = self.gray_low <= similarity <= self.gray_high

        reranker_score = None
        reranker_model = None
        reranker_backend = None
        decision_stage = "embedding"
        allow = True
        risk_type = "none"
        reason = "action aligned with current goal"
        confidence = "medium"

        if explicit_injection and is_high_risk and not tag_match:
            allow = False
            risk_type = "prompt_injection"
            decision_stage = "rules"
            reason = f"external content attempts to steer {permission_key} away from the original goal"
            confidence = "high"
        else:
            if is_high_risk or in_gray_zone:
                rerank = self._score_reranker(goal_text, action_text, embedding.score)
                reranker_score = rerank["score"]
                reranker_model = rerank["model"]
                reranker_backend = rerank["backend"]
                decision_stage = "reranker"

            if not tag_match:
                if similarity < self.threshold and reranker_score is None:
                    allow = False
                    risk_type = "intent_mismatch"
                    reason = f"user goal is '{goal_text}' but action is '{permission_key}'"
                    confidence = "high"
                elif reranker_score is not None and similarity < self.threshold and reranker_score < self.reranker_threshold:
                    allow = False
                    risk_type = "intent_mismatch"
                    reason = f"user goal is '{goal_text}' but action is '{permission_key}'"
                    confidence = "high"
                elif reranker_score is not None and in_gray_zone and reranker_score < self.reranker_threshold:
                    allow = False
                    risk_type = "intent_mismatch"
                    reason = f"gray-zone request failed reranker review for {permission_key}"
                    confidence = "medium"

        return GuardDecision(
            allowed=allow,
            risk_type=risk_type,
            intent_similarity=round(similarity, 4),
            threshold=round(self.threshold, 4),
            gray_zone=(round(self.gray_low, 4), round(self.gray_high, 4)),
            embedding_model=embedding.model_name,
            embedding_backend=embedding.backend,
            reranker_model=reranker_model,
            reranker_backend=reranker_backend,
            reranker_score=round(reranker_score, 4) if reranker_score is not None else None,
            reranker_threshold=round(self.reranker_threshold, 4) if reranker_score is not None else None,
            decision_stage=decision_stage,
            reason=reason,
            confidence=confidence,
            trust_labels={
                "system": "high",
                "user": "medium",
                "external": "low" if request.context.external_text or request.context.source_summary else "none",
            },
        )

    def _load_calibration(self) -> None:
        samples = read_json(self.calibration_fixture, [])
        fixture_hash = sha256_text(json.dumps(samples, ensure_ascii=False, sort_keys=True))
        report = read_json(self.calibration_report, {})
        if (
            report
            and report.get("fixture_hash") == fixture_hash
            and report.get("embedding_model") == self.encoder.model_name
            and report.get("reranker_model") == self.reranker.model_name
            and report.get("embedding_has_signal") is True
            and report.get("reranker_has_signal") is True
        ):
            self.threshold = float(report["threshold"])
            self.gray_low = float(report["gray_low"])
            self.gray_high = float(report["gray_high"])
            self.reranker_threshold = float(report["reranker_threshold"])
            return

        if not samples:
            return

        embedding_scores: list[tuple[float, bool]] = []
        reranker_scores: list[tuple[float, bool]] = []
        for item in samples:
            goal_text = item["user_goal"]
            action_text = ACTION_DESCRIPTIONS[item["action_key"]]
            is_benign = item["label"] == "benign"
            embedding_score = self.encoder.score(goal_text, action_text).score
            reranker_score = self._score_reranker(goal_text, action_text, embedding_score)["score"]
            embedding_scores.append((embedding_score, is_benign))
            reranker_scores.append((reranker_score, is_benign))

        embedding_has_signal = self._has_signal(embedding_scores)
        reranker_has_signal = self._has_signal(reranker_scores)

        if embedding_has_signal:
            self.threshold = self._best_threshold(embedding_scores)
            margin = self._gray_margin(embedding_scores, self.threshold)
            self.gray_low = max(0.0, self.threshold - margin)
            self.gray_high = min(1.0, self.threshold + margin)
        else:
            self.threshold = 0.65
            self.gray_low = 0.6
            self.gray_high = 0.7

        if reranker_has_signal:
            self.reranker_threshold = self._best_threshold(reranker_scores)
        else:
            self.reranker_threshold = 0.5

        write_json(
            self.calibration_report,
            {
                "embedding_model": self.encoder.model_name,
                "reranker_model": self.reranker.model_name,
                "fixture_hash": fixture_hash,
                "threshold": round(self.threshold, 4),
                "gray_low": round(self.gray_low, 4),
                "gray_high": round(self.gray_high, 4),
                "reranker_threshold": round(self.reranker_threshold, 4),
                "embedding_has_signal": embedding_has_signal,
                "reranker_has_signal": reranker_has_signal,
                "samples": samples,
                "embedding_scores": [
                    {"score": round(score, 4), "label": "benign" if is_benign else "malicious"}
                    for score, is_benign in embedding_scores
                ],
                "reranker_scores": [
                    {"score": round(score, 4), "label": "benign" if is_benign else "malicious"}
                    for score, is_benign in reranker_scores
                ],
            },
        )

    def _score_reranker(self, goal_text: str, action_text: str, fallback_score: float) -> dict[str, str | float]:
        try:
            rerank = self.reranker.score(goal_text, action_text)
            return {
                "score": rerank.score,
                "model": rerank.model_name,
                "backend": rerank.backend,
            }
        except Exception:
            return {
                "score": fallback_score,
                "model": self.encoder.model_name,
                "backend": f"embedding_proxy:{self.encoder.backend}",
            }

    def _contains_injection(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in INJECTION_MARKERS)

    def _infer_goal_tags(self, text: str) -> set[str]:
        lowered = text.lower()
        tags: set[str] = set()
        if any(keyword in lowered for keyword in ("总结", "summary", "邮件", "mail")):
            tags.update({"mail", "summary"})
        if any(keyword in lowered for keyword in ("余额", "balance", "账户", "bank")):
            tags.update({"finance", "query"})
        if any(keyword in lowered for keyword in ("转账", "transfer", "付款")):
            tags.update({"finance", "transfer"})
        if any(keyword in lowered for keyword in ("相册", "照片", "证件", "文件", "gallery", "资料")):
            tags.update({"gallery", "privacy"})
        if any(keyword in lowered for keyword in ("天气", "weather", "出差", "行程")):
            tags.add("weather")
        if any(keyword in lowered for keyword in ("记忆", "memory", "提示词", "prompt", "配置", "config", "状态")):
            tags.update({"state", "memory", "prompt", "config"})
        return tags

    @staticmethod
    def _has_signal(samples: list[tuple[float, bool]]) -> bool:
        if not samples:
            return False
        values = [score for score, _ in samples]
        return max(values) - min(values) > 1e-6

    @staticmethod
    def _best_threshold(samples: list[tuple[float, bool]]) -> float:
        scores = sorted({score for score, _ in samples})
        if not scores:
            return 0.65
        candidates = [max(0.0, scores[0] - 1e-3)]
        candidates.extend((left + right) / 2 for left, right in zip(scores, scores[1:]))
        candidates.append(min(1.0, scores[-1] + 1e-3))

        best_threshold = candidates[0]
        best_correct = -1
        for threshold in candidates:
            correct = sum((score >= threshold) == is_benign for score, is_benign in samples)
            if correct > best_correct:
                best_correct = correct
                best_threshold = threshold
        return best_threshold

    @staticmethod
    def _gray_margin(samples: list[tuple[float, bool]], threshold: float) -> float:
        distances = sorted(abs(score - threshold) for score, _ in samples if abs(score - threshold) > 1e-6)
        if not distances:
            return 0.05
        return min(0.08, max(0.03, distances[0]))

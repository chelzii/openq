from __future__ import annotations

from app.schemas import DemoPresentation, DemoRunResponse, DemoXRayPresentation


def blocked_layer_label(blocked_layer: str | None) -> str:
    labels = {
        "guard": "意图护栏",
        "chain": "链上权限",
        "state": "状态防护",
        "planning": "规划层",
        "approval": "人工审批",
        "system": "系统保护",
    }
    return labels.get(blocked_layer or "", blocked_layer or "系统")


def _last_response(result: DemoRunResponse) -> dict:
    if not result.traces:
        return {}
    return result.traces[-1].response.model_dump(mode="json")


def _auth_context(result: DemoRunResponse) -> dict:
    return (_last_response(result).get("auth") or {}) if result.traces else {}


def _state_context(result: DemoRunResponse) -> dict:
    return (_last_response(result).get("state_change") or {}) if result.traces else {}


def _is_benign(result: DemoRunResponse) -> bool:
    return result.scenario.scenario_type == "benign"


def _is_chain_offline(result: DemoRunResponse) -> bool:
    return result.scenario.scenario_type == "chain_offline"


def final_status_text(result: DemoRunResponse) -> str:
    if result.final_status == "executed":
        return "执行完成"
    if result.final_status == "blocked":
        return f"已阻断 · {blocked_layer_label(result.blocked_layer)}" if result.blocked_layer else "已阻断"
    if result.final_status == "planning_debug":
        return "规划诊断已捕获"
    if result.final_status == "planning_failed":
        return "规划失败"
    if result.final_status == "error":
        return "系统错误"
    return str(result.final_status or "未知状态").upper()


def blocked_verdict_text(result: DemoRunResponse) -> str:
    auth = _auth_context(result)
    if result.blocked_layer == "chain":
        if auth.get("chain_available") is False:
            return "⚠️ 正常请求因链不可用被 fail-closed 阻断" if _is_benign(result) else "⚠️ 请求因链不可用被 fail-closed 阻断"
        if auth.get("reason") == "signature_invalid":
            return "⛔ 非法签名已被链上验签阻断"
        if auth.get("reason") == "permission_denied":
            return "⚠️ 正常请求被链上权限拦截" if _is_benign(result) else "⛔ 攻击已被链上权限阻断"
    if result.blocked_layer == "guard":
        return "⚠️ 正常请求被意图护栏拦截" if _is_benign(result) else "⛔ 攻击已被意图护栏阻断"
    if result.blocked_layer == "state":
        return "⚠️ 状态写入触发保护，已被挂起或回滚" if _is_benign(result) else "⛔ 高危写入已被状态防护阻断"
    if _is_benign(result):
        return f"⚠️ 正常请求已被 {blocked_layer_label(result.blocked_layer)}阻断"
    return f"⛔ 攻击已被成功阻断 ({blocked_layer_label(result.blocked_layer)}拦截)"


def final_status_hint_text(result: DemoRunResponse) -> str:
    auth = _auth_context(result)
    if result.final_status == "executed":
        if auth.get("degraded_allowed"):
            return "链上暂不可用，但该请求属于只读动作，系统按受控降级策略放行执行。"
        if _is_chain_offline(result):
            return "该场景用于验证链离线时的只读降级放行，当前执行结果符合预期。"
        summary = _last_response(result).get("result", {}).get("summary")
        return f"执行结果: {summary}。绿色不等于“无需看日志”。" if summary else "请求已通过当前防线并被执行。绿色不等于“无需看日志”。"
    if result.final_status == "blocked":
        if result.blocked_layer == "chain" and auth.get("chain_available") is False:
            return f"链上网关当前不可用 ({auth.get('reason') or 'unknown'})，系统按 fail-closed 策略阻断了本次请求。"
        if result.blocked_layer == "chain" and auth.get("reason") == "signature_invalid":
            return "请求在链上验签阶段被识别为非法签名，因此未继续执行。"
        if result.blocked_layer == "chain" and auth.get("reason") == "permission_denied":
            return "请求已通过签名校验，但在链上权限校验阶段被拒绝。"
        if result.blocked_layer == "state":
            return "前置防线已放行，但高危状态写入在受保护状态层被挂起或回滚。"
        return f"请求已在 {blocked_layer_label(result.blocked_layer)}层阻断。" if result.blocked_layer else "请求已被系统阻断。"
    if result.final_status == "planning_debug":
        planning_error = result.openclaw_plan.planning_error
        prefix = f"诊断结论: {planning_error}" if planning_error else "real_debug 已捕获 OpenClaw 输出。"
        return f"{prefix} 当前模式只保留原始响应和诊断信息，不进入执行阶段。"
    if result.final_status == "planning_failed":
        planning_error = result.openclaw_plan.planning_error
        prefix = f"规划失败原因: {planning_error}" if planning_error else "OpenClaw 未返回可执行计划。"
        return f"{prefix} 正式链路未进入执行阶段。"
    if result.final_status == "error":
        return "系统在处理过程中出现异常，请查看本次执行日志和审计记录。"
    return "请结合本次执行日志判断结果。"


def verdict_title_text(result: DemoRunResponse) -> str:
    if result.final_status == "executed":
        if _is_chain_offline(result):
            return "🛟 链离线下已按只读降级策略放行"
        if _is_benign(result):
            return "✅ 正常请求：执行成功"
        return "⚠️ 危险：攻击已被越权执行"
    if result.final_status == "blocked":
        return blocked_verdict_text(result)
    if result.final_status == "planning_failed":
        return f"❌ OpenClaw 规划失败，正式链路已 fail-closed ({result.planning_failed_reason or 'unknown'})"
    if result.final_status == "planning_debug":
        return "🧪 OpenClaw 诊断模式：仅捕获规划，不执行"
    return "❌ 系统执行异常"


def xray_presentation(result: DemoRunResponse) -> DemoXRayPresentation:
    if result.final_status in {"planning_failed", "planning_debug"} or not result.traces:
        guard_status = "规划诊断" if result.final_status == "planning_debug" else "规划失败"
        return DemoXRayPresentation(
            guard_status=guard_status,
            chain_status="未到达",
            sandbox_status="未执行",
        )

    auth = _auth_context(result)
    state = _state_context(result)

    guard_status = "已拦截" if result.blocked_layer == "guard" else "已放行"

    if result.blocked_layer == "guard":
        chain_status = "未到达"
    elif auth.get("chain_available") is False:
        chain_status = "链降级放行" if auth.get("degraded_allowed") else "链不可用"
    elif result.blocked_layer == "chain":
        chain_status = "验权失败"
    else:
        chain_status = "校验通过"

    if result.blocked_layer in {"guard", "chain"}:
        sandbox_status = "未执行"
    elif result.blocked_layer == "state":
        if state.get("approval_status") == "rejected":
            sandbox_status = "审批拒绝"
        elif state.get("rollback_performed"):
            sandbox_status = "已回滚"
        else:
            sandbox_status = "待审批"
    else:
        sandbox_status = "执行完成"

    return DemoXRayPresentation(
        guard_status=guard_status,
        chain_status=chain_status,
        sandbox_status=sandbox_status,
    )


def build_demo_presentation(result: DemoRunResponse) -> DemoPresentation:
    return DemoPresentation(
        final_status_text=final_status_text(result),
        final_hint_text=final_status_hint_text(result),
        verdict_title=verdict_title_text(result),
        verdict_summary=result.evidence_summary or result.summary,
        xray=xray_presentation(result),
    )

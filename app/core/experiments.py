from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.chain.crypto import RequestSigner
from app.chain.fisco import FiscoBcosService
from app.core.openclaw import OpenClawFacade
from app.core.utils import read_json, write_csv, write_json
from app.schemas import (
    CallAppRequest,
    DemoRunRequest,
    DemoRunResponse,
    ExecutionTrace,
    ExperimentAggregate,
    ExperimentRecord,
    ExperimentReport,
    Mode,
    ScenarioDefinition,
)


@dataclass
class DemoOrchestrator:
    signer: RequestSigner
    chain: FiscoBcosService
    gateway: any
    openclaw: OpenClawFacade
    scenario_fixture: any

    def scenarios(self) -> list[ScenarioDefinition]:
        payload = read_json(self.scenario_fixture, [])
        return [ScenarioDefinition.model_validate(item) for item in payload]

    def scenario_map(self) -> dict[str, ScenarioDefinition]:
        return {scenario.scenario_id: scenario for scenario in self.scenarios()}

    async def run_demo(self, request: DemoRunRequest) -> DemoRunResponse:
        scenario = self.scenario_map()[request.scenario_id].model_copy(deep=True)
        if request.user_task_override:
            scenario.user_task = request.user_task_override
        identity = self.chain.demo_identity()
        assistant_reply = await self.openclaw.generate_reply(scenario, request.mode.value, request.use_real_openclaw)
        traces: list[ExecutionTrace] = []
        blocked_layer = None
        final_status = "executed"
        summary = ""

        for index, step in enumerate(scenario.steps, start=1):
            payload = {
                "request_id": f"{scenario.scenario_id}-{request.mode.value}-{index}-{uuid.uuid4().hex[:8]}",
                "session_id": request.session_id,
                "mode": request.mode.value,
                "did": identity.did,
                "resource_type": step.resource_type.value,
                "app": step.app,
                "action": step.action,
                "args": step.args,
                "context": {
                    "user_goal": scenario.user_task,
                    "trusted_system_goal": scenario.trusted_system_goal,
                    "source_summary": scenario.source_summary,
                    "external_text": scenario.external_text,
                    "scenario_id": scenario.scenario_id,
                },
                "metadata": step.metadata,
            }
            payload_hash = self.signer.payload_hash(payload)
            signature = self.signer.sign_payload(payload, identity.private_key)
            call_request = CallAppRequest.model_validate({**payload, "payload_hash": payload_hash, "signature": signature})
            response = self.gateway.handle_call(call_request, approval_token=request.approval_token)
            traces.append(ExecutionTrace(step_index=index, request=call_request, response=response))
            if response.status != "executed":
                final_status = response.status
                blocked_layer = response.blocked_layer
                break

        summary = self._summarize(scenario, request.mode, traces)
        return DemoRunResponse(
            scenario=scenario,
            mode=request.mode,
            assistant_reply=assistant_reply,
            traces=traces,
            final_status=final_status,
            blocked_layer=blocked_layer,
            summary=summary,
        )

    async def run_experiments(self) -> ExperimentReport:
        records: list[ExperimentRecord] = []
        self._reset_state()
        for scenario in self.scenarios():
            for mode in (Mode.OFF, Mode.GUARD_ONLY, Mode.FULL):
                started = time.perf_counter()
                response = await self.run_demo(
                    DemoRunRequest(
                        scenario_id=scenario.scenario_id,
                        mode=mode,
                        session_id=f"exp-{scenario.scenario_id}",
                        use_real_openclaw=False,
                    )
                )
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                last_trace = response.traces[-1]
                actual_result = "executed" if response.final_status == "executed" else "blocked"
                expected_result = scenario.expected_by_mode[mode.value]
                false_positive = scenario.scenario_type == "benign" and actual_result == "blocked"
                records.append(
                    ExperimentRecord(
                        sample_id=scenario.scenario_id,
                        scenario_type=scenario.scenario_type,
                        mode=mode,
                        request_id=last_trace.request.request_id,
                        expected_result=expected_result,
                        actual_result=actual_result,
                        blocked_layer=response.blocked_layer,
                        latency_ms=latency_ms,
                        false_positive=false_positive,
                        audit_written=bool(last_trace.response.audit),
                        intent_similarity=last_trace.response.guard.intent_similarity
                        if last_trace.response.guard
                        else None,
                        chain_backend=last_trace.response.auth.backend if last_trace.response.auth else None,
                        chain_available=last_trace.response.auth.chain_available if last_trace.response.auth else None,
                        state_alert=bool(last_trace.response.state_change and last_trace.response.state_change.suspicious),
                    )
                )
                self._reset_state()
        aggregates = self._aggregate(records)
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        json_path = self.scenario_fixture.parent.parent / "experiments" / f"report-{timestamp}.json"
        csv_path = self.scenario_fixture.parent.parent / "experiments" / f"report-{timestamp}.csv"
        write_json(json_path, [record.model_dump(mode="json") for record in records])
        write_csv(
            csv_path,
            [
                "sample_id",
                "scenario_type",
                "mode",
                "request_id",
                "expected_result",
                "actual_result",
                "blocked_layer",
                "latency_ms",
                "false_positive",
                "audit_written",
                "intent_similarity",
                "chain_backend",
                "chain_available",
                "state_alert",
            ],
            [record.model_dump(mode="json") for record in records],
        )
        return ExperimentReport(records=records, aggregates=aggregates, exported_json=str(json_path), exported_csv=str(csv_path))

    def _aggregate(self, records: list[ExperimentRecord]) -> list[ExperimentAggregate]:
        aggregates: list[ExperimentAggregate] = []
        for mode in (Mode.OFF, Mode.GUARD_ONLY, Mode.FULL):
            subset = [record for record in records if record.mode == mode]
            total = len(subset)
            blocked = sum(1 for record in subset if record.actual_result == "blocked")
            executed = total - blocked
            correct = sum(1 for record in subset if record.actual_result == record.expected_result)
            false_positive_rate = round(sum(1 for record in subset if record.false_positive) / total, 3) if total else 0.0
            attack_cases = [record for record in subset if record.scenario_type != "benign"]
            state_cases = [record for record in subset if record.scenario_type in {"memory_poisoning", "prompt_poisoning"}]
            interception_rate = (
                round(
                    sum(1 for record in attack_cases if record.actual_result == "blocked") / len(attack_cases),
                    3,
                )
                if attack_cases
                else 0.0
            )
            state_detection_rate = (
                round(sum(1 for record in state_cases if record.state_alert) / len(state_cases), 3)
                if state_cases
                else 0.0
            )
            avg_latency = round(sum(record.latency_ms for record in subset) / total, 2) if total else 0.0
            aggregates.append(
                ExperimentAggregate(
                    mode=mode,
                    total=total,
                    blocked=blocked,
                    executed=executed,
                    correct=correct,
                    false_positive_rate=false_positive_rate,
                    interception_rate=interception_rate,
                    state_detection_rate=state_detection_rate,
                    avg_latency_ms=avg_latency,
                )
            )
        return aggregates

    def _summarize(self, scenario: ScenarioDefinition, mode: Mode, traces: list[ExecutionTrace]) -> str:
        last_response = traces[-1].response
        if last_response.status != "executed":
            return f"{scenario.title} 在 {mode.value} 模式下被 {last_response.blocked_layer} 层拦截。"
        if scenario.scenario_type == "benign":
            return f"{scenario.title} 正常完成，系统返回安全结果。"
        return f"{scenario.title} 在 {mode.value} 模式下未被拦截，说明当前配置存在风险暴露。"

    def _reset_state(self) -> None:
        baseline_targets = {
            "memory/main.md": "# Long Term Memory\n\n- 当前联系人: trusted-vendor\n- 默认任务: 先总结，再判断是否需要后续动作。\n",
            "prompt/shared.txt": "你是 OpenQ 的受保护共享提示词。\n你只能通过统一网关访问虚拟 App，并遵循最小权限原则。\n",
            "config/system.json": '{\n  "transfer_limit": 2000,\n  "allow_gallery_read": false\n}\n',
        }
        for target, content in baseline_targets.items():
            self.gateway.dispatcher.state_store.write(target, content)
        self.gateway.dispatcher.integrity.refresh_baselines()

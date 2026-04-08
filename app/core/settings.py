from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    state_dir: Path
    audit_log: Path
    audit_export_dir: Path
    chain_registry: Path
    signer_identity: Path
    chain_console_script: Path
    chain_contract_source: Path
    chain_console_contract_dir: Path
    baseline_file: Path
    approval_store: Path
    guard_calibration_report: Path
    scenario_fixture: Path
    intent_calibration_fixture: Path
    messages_fixture: Path
    assets_fixture: Path
    templates_dir: Path
    static_dir: Path
    real_openclaw_url: str = "ws://localhost:18789"
    chain_probe_ports: tuple[int, ...] = (20200, 20201)

    @classmethod
    def load(cls) -> "Settings":
        root_dir = Path(__file__).resolve().parents[2]
        data_dir = root_dir / "data"
        return cls(
            root_dir=root_dir,
            data_dir=data_dir,
            state_dir=data_dir / "state",
            audit_log=data_dir / "audit" / "audit.jsonl",
            audit_export_dir=data_dir / "experiments",
            chain_registry=data_dir / "chain" / "registry.json",
            signer_identity=data_dir / "chain" / "demo_identity.json",
            chain_console_script=root_dir / "scripts" / "fisco-console.sh",
            chain_contract_source=root_dir / "contracts" / "OpenQRegistry.sol",
            chain_console_contract_dir=root_dir / "runtime-deps" / "fisco-portable" / "console" / "contracts" / "solidity",
            baseline_file=data_dir / "baselines" / "state_hashes.json",
            approval_store=data_dir / "state" / "approvals.json",
            guard_calibration_report=data_dir / "baselines" / "guard_calibration.json",
            scenario_fixture=data_dir / "fixtures" / "scenarios.json",
            intent_calibration_fixture=data_dir / "fixtures" / "intent_calibration.json",
            messages_fixture=data_dir / "fixtures" / "messages.json",
            assets_fixture=data_dir / "fixtures" / "assets.json",
            templates_dir=root_dir / "app" / "demo" / "templates",
            static_dir=root_dir / "app" / "demo" / "static",
        )

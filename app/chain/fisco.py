from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from app.chain.crypto import Identity, RequestSigner
from app.core.utils import ensure_parent, read_json, sha256_json, write_json
from app.schemas import ChainStatusView


CONTRACT_NAME = "OpenQRegistry"
SEED_VERSION = 2
DEFAULT_IDENTITY_PERMISSIONS = {
    "agent": {
        "mail.list_messages": True,
        "mail.read_message": True,
        "bank.get_balance": True,
        "bank.transfer": False,
        "gallery.list_assets": True,
        "gallery.read_asset": False,
        "weather.get_weather": True,
        "weather.get_alert": True,
        "state.update_memory": True,
        "state.update_prompt": True,
        "state.update_config": True,
        "approval.decide": False,
    },
    "approver": {
        "mail.list_messages": True,
        "mail.read_message": True,
        "bank.get_balance": True,
        "bank.transfer": False,
        "gallery.list_assets": True,
        "gallery.read_asset": False,
        "weather.get_weather": True,
        "weather.get_alert": True,
        "state.update_memory": True,
        "state.update_prompt": True,
        "state.update_config": True,
        "approval.decide": True,
    },
}


@dataclass
class ChainAuthorization:
    verified: bool
    permission_allowed: bool
    backend: str
    reason: str
    permission_key: str
    chain_available: bool
    block_number: int | None = None


@dataclass
class ChainRuntimeStatus:
    available: bool
    backend: str
    message: str
    checked_at: str
    endpoints: list[str]
    block_number: int | None = None


class ChainConsoleError(RuntimeError):
    pass


class FiscoBcosService:
    def __init__(
        self,
        registry_path: Path,
        identity_path: Path,
        contract_source: Path,
        console_contract_dir: Path,
        console_script: Path | None = None,
        probe_ports: tuple[int, ...] = (20200, 20201),
    ):
        self.registry_path = registry_path
        self.identity_path = identity_path
        self.identities_path = identity_path if identity_path.name == "identities.json" else identity_path.with_name("identities.json")
        self.contract_source = contract_source
        self.console_contract_dir = console_contract_dir
        self.console_script = console_script
        self.probe_ports = probe_ports
        self.backend_name = "fisco_bcos_contract_registry"
        self._cached_status: ChainRuntimeStatus | None = None
        self._cached_at = 0.0
        self._registry_ready = False
        self._ensure_seed_identities()

    def _ensure_seed_identities(self) -> None:
        payload: dict[str, dict[str, str]] = {}
        if self.identities_path.exists():
            raw = read_json(self.identities_path, {})
            if isinstance(raw, dict):
                payload = {
                    key: value
                    for key, value in raw.items()
                    if isinstance(value, dict) and {"did", "label", "public_key", "private_key"} <= set(value)
                }

        if not payload and self.identity_path.exists() and self.identity_path != self.identities_path:
            legacy = read_json(self.identity_path, {})
            if isinstance(legacy, dict) and {"did", "label", "public_key", "private_key"} <= set(legacy):
                payload["agent"] = {
                    "did": legacy["did"],
                    "label": legacy["label"],
                    "public_key": legacy["public_key"],
                    "private_key": legacy["private_key"],
                }

        if "agent" not in payload:
            agent = RequestSigner.generate_identity("did:openq:agent-001", "OpenQ Demo Agent")
            payload["agent"] = {
                "did": agent.did,
                "label": agent.label,
                "public_key": agent.public_key,
                "private_key": agent.private_key,
            }

        if "approver" not in payload:
            approver = RequestSigner.generate_identity("did:openq:approver-001", "OpenQ Approval Operator")
            payload["approver"] = {
                "did": approver.did,
                "label": approver.label,
                "public_key": approver.public_key,
                "private_key": approver.private_key,
            }

        write_json(self.identities_path, payload)

    def _registry_state(self) -> dict[str, Any]:
        return read_json(
            self.registry_path,
            {
                "backend": self.backend_name,
                "contract_name": CONTRACT_NAME,
                "contract_address": None,
                "seed_version": 0,
                "deploy_tx_hash": None,
            },
        )

    def _write_registry_state(self, payload: dict[str, Any]) -> None:
        write_json(self.registry_path, payload)

    def identities(self) -> dict[str, Identity]:
        payload = read_json(self.identities_path, {})
        return {
            key: Identity(
                did=item["did"],
                private_key=item["private_key"],
                public_key=item["public_key"],
                label=item["label"],
            )
            for key, item in payload.items()
        }

    def identity(self, key: str = "agent") -> Identity:
        identities = self.identities()
        identity = identities.get(key)
        if identity is None:
            raise KeyError(f"unknown identity key: {key}")
        return identity

    def demo_identity(self) -> Identity:
        return self.identity("agent")

    def approver_identity(self) -> Identity:
        return self.identity("approver")

    def _sync_contract_source(self) -> None:
        if not self.contract_source.exists():
            raise ChainConsoleError(f"contract source missing: {self.contract_source}")
        destination = self.console_contract_dir / self.contract_source.name
        ensure_parent(destination)
        source_text = self.contract_source.read_text(encoding="utf-8")
        if not destination.exists() or destination.read_text(encoding="utf-8") != source_text:
            shutil.copyfile(self.contract_source, destination)

    @staticmethod
    def _quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _run_console(self, commands: list[str], timeout: int = 90) -> str:
        if self.console_script is None or not self.console_script.exists():
            raise ChainConsoleError("console_unavailable")
        completed = subprocess.run(
            [str(self.console_script), "group0"],
            input="\n".join(commands + ["exit"]) + "\n",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if "Failed to create BcosSDK failed!" in combined:
            raise ChainConsoleError("console_connect_failed")
        return combined

    @staticmethod
    def _extract_last(pattern: str, text: str) -> str | None:
        matches = re.findall(pattern, text, flags=re.MULTILINE)
        if not matches:
            return None
        result = matches[-1]
        return result if isinstance(result, str) else result[-1]

    def _query_block_number(self) -> tuple[int | None, str]:
        try:
            output = self._run_console(["getBlockNumber"], timeout=30)
        except ChainConsoleError as exc:
            return None, str(exc)
        block_raw = self._extract_last(r"^\[group0\]: /apps>\s*(\d+)\s*$", output)
        if block_raw is None:
            return None, "console_no_block_number"
        return int(block_raw), "live"

    def runtime_status(self, force_refresh: bool = False) -> ChainRuntimeStatus:
        now = time.time()
        if not force_refresh and self._cached_status and now - self._cached_at < 5:
            return self._cached_status
        block_number, probe_message = self._query_block_number()
        available = block_number is not None
        status = ChainRuntimeStatus(
            available=available,
            backend=self.backend_name if available else "fisco_bcos_unavailable",
            block_number=block_number,
            message="live block query ok" if available else probe_message,
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            endpoints=[f"127.0.0.1:{port}" for port in self.probe_ports],
        )
        self._cached_status = status if available else None
        self._cached_at = now if available else 0.0
        return status

    def status_view(self, force_refresh: bool = False) -> ChainStatusView:
        status = self.runtime_status(force_refresh=force_refresh)
        return ChainStatusView(
            available=status.available,
            backend=status.backend,
            block_number=status.block_number,
            message=status.message,
            checked_at=status.checked_at,
            endpoints=status.endpoints,
        )

    def _call_return_values(self, command: str) -> list[str]:
        output = self._run_console([command])
        if '"code":' in output and "Return values:" not in output:
            raise ChainConsoleError(output.strip())
        raw = self._extract_last(r"Return values:\((.*)\)", output)
        if raw is None:
            raise ChainConsoleError(output.strip())
        if raw.strip() == "":
            return []
        return [item.strip() for item in raw.split(",")]

    def _call_transaction_hash(self, command: str) -> str:
        output = self._run_console([command])
        tx_hash = self._extract_last(r"transaction hash:\s*(0x[0-9a-fA-F]+)", output)
        if tx_hash is None:
            raise ChainConsoleError(output.strip())
        return tx_hash

    def _contract_address(self) -> str:
        self._ensure_registry_ready()
        state = self._registry_state()
        address = state.get("contract_address")
        if not address:
            raise ChainConsoleError("registry_contract_unavailable")
        return str(address)

    def _deploy_registry_contract(self) -> str:
        self._sync_contract_source()
        output = self._run_console([f"deploy {CONTRACT_NAME}"], timeout=180)
        contract_address = self._extract_last(r"contract address:\s*(0x[0-9a-fA-F]+)", output)
        tx_hash = self._extract_last(r"transaction hash:\s*(0x[0-9a-fA-F]+)", output)
        if contract_address is None or tx_hash is None:
            raise ChainConsoleError(output.strip())
        state = self._registry_state()
        state.update(
            {
                "backend": self.backend_name,
                "contract_name": CONTRACT_NAME,
                "contract_address": contract_address,
                "deploy_tx_hash": tx_hash,
            }
        )
        self._write_registry_state(state)
        return contract_address

    def _read_identity(self, contract_address: str, did: str) -> tuple[bool, str, str]:
        values = self._call_return_values(
            f"call {CONTRACT_NAME} {contract_address} getIdentity {self._quote(did)}"
        )
        if len(values) != 3:
            raise ChainConsoleError(f"unexpected getIdentity output: {values}")
        return values[0].lower() == "true", values[1], values[2]

    def _read_permission(self, contract_address: str, did: str, permission_key: str) -> tuple[bool, bool]:
        values = self._call_return_values(
            f"call {CONTRACT_NAME} {contract_address} getPermission {self._quote(did)} {self._quote(permission_key)}"
        )
        if len(values) != 2:
            raise ChainConsoleError(f"unexpected getPermission output: {values}")
        return values[0].lower() == "true", values[1].lower() == "true"

    def _seed_registry(self, contract_address: str) -> None:
        for identity_key, permissions in DEFAULT_IDENTITY_PERMISSIONS.items():
            identity = self.identity(identity_key)
            exists, public_key, label = self._read_identity(contract_address, identity.did)
            if not exists or public_key != identity.public_key or label != identity.label:
                self._call_transaction_hash(
                    " ".join(
                        [
                            f"call {CONTRACT_NAME}",
                            contract_address,
                            "registerIdentity",
                            self._quote(identity.did),
                            self._quote(identity.public_key),
                            self._quote(identity.label),
                        ]
                    )
                )
            for permission_key, allowed in permissions.items():
                chain_exists, chain_allowed = self._read_permission(contract_address, identity.did, permission_key)
                if not chain_exists or chain_allowed != allowed:
                    allowed_literal = "true" if allowed else "false"
                    self._call_transaction_hash(
                        " ".join(
                            [
                                f"call {CONTRACT_NAME}",
                                contract_address,
                                "setPermission",
                                self._quote(identity.did),
                                self._quote(permission_key),
                                allowed_literal,
                            ]
                        )
                    )
        state = self._registry_state()
        state["seed_version"] = SEED_VERSION
        self._write_registry_state(state)

    def _ensure_registry_ready(self) -> None:
        if self._registry_ready:
            return
        status = self.runtime_status()
        if not status.available:
            raise ChainConsoleError(status.message)
        state = self._registry_state()
        contract_address = state.get("contract_address")
        if contract_address:
            try:
                self._call_return_values(f"call {CONTRACT_NAME} {contract_address} getAuditCount")
            except ChainConsoleError:
                contract_address = None
        if contract_address and state.get("seed_version") == SEED_VERSION:
            self._registry_ready = True
            return
        if not contract_address:
            contract_address = self._deploy_registry_contract()
        self._seed_registry(contract_address)
        self._registry_ready = True

    def warmup_registry(self) -> None:
        self._ensure_registry_ready()

    def authorize(self, signed_payload: dict[str, Any], signature: str, did: str, permission_key: str) -> ChainAuthorization:
        status = self.runtime_status()
        if not status.available:
            return ChainAuthorization(
                verified=False,
                permission_allowed=False,
                backend=status.backend,
                reason=status.message,
                permission_key=permission_key,
                chain_available=False,
                block_number=status.block_number,
            )
        try:
            contract_address = self._contract_address()
            exists, public_key, _ = self._read_identity(contract_address, did)
            if not exists:
                return ChainAuthorization(
                    verified=False,
                    permission_allowed=False,
                    backend=status.backend,
                    reason="unknown_did",
                    permission_key=permission_key,
                    chain_available=True,
                    block_number=status.block_number,
                )
            verified = RequestSigner.verify_payload(signed_payload, signature, public_key)
            if not verified:
                return ChainAuthorization(
                    verified=False,
                    permission_allowed=False,
                    backend=status.backend,
                    reason="signature_invalid",
                    permission_key=permission_key,
                    chain_available=True,
                    block_number=status.block_number,
                )
            _, allowed = self._read_permission(contract_address, did, permission_key)
            return ChainAuthorization(
                verified=True,
                permission_allowed=allowed,
                backend=status.backend,
                reason="allowed" if allowed else "permission_denied",
                permission_key=permission_key,
                chain_available=True,
                block_number=status.block_number,
            )
        except ChainConsoleError as exc:
            return ChainAuthorization(
                verified=False,
                permission_allowed=False,
                backend=status.backend,
                reason=str(exc),
                permission_key=permission_key,
                chain_available=True,
                block_number=status.block_number,
            )

    def record_audit(self, request_id: str, payload: dict[str, Any]) -> tuple[str, int | None, str]:
        status = self.runtime_status()
        if not status.available:
            raise ChainConsoleError(status.message)
        contract_address = self._contract_address()
        digest = sha256_json({"request_id": request_id, **payload})
        tx_hash = self._call_transaction_hash(
            " ".join(
                [
                    f"call {CONTRACT_NAME}",
                    contract_address,
                    "recordAudit",
                    self._quote(request_id),
                    self._quote(digest),
                    self._quote(str(payload.get("status", ""))),
                    self._quote(str(payload.get("blocked_layer", ""))),
                    self._quote(str(payload.get("app", ""))),
                    self._quote(str(payload.get("action", ""))),
                ]
            )
        )
        block_number = self.runtime_status(force_refresh=True).block_number
        return tx_hash, block_number, status.backend

    def audit_records(self, limit: int = 50) -> list[dict[str, Any]]:
        status = self.runtime_status()
        if not status.available:
            return []
        try:
            contract_address = self._contract_address()
            count_values = self._call_return_values(f"call {CONTRACT_NAME} {contract_address} getAuditCount")
            if len(count_values) != 1:
                return []
            count = int(count_values[0])
            records: list[dict[str, Any]] = []
            start_index = max(0, count - limit)
            for index in range(start_index, count):
                values = self._call_return_values(f"call {CONTRACT_NAME} {contract_address} getAuditByIndex {index}")
                if len(values) != 8:
                    continue
                records.append(
                    {
                        "request_id": values[0],
                        "digest": values[1],
                        "status": values[2],
                        "blocked_layer": values[3],
                        "app": values[4],
                        "action": values[5],
                        "block_number": int(values[6]),
                        "recorded_at": int(values[7]),
                    }
                )
            return records
        except Exception:
            return []

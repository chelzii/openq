from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.schemas import ScenarioDefinition


PAIRED_DEVICE = {
    "private_key": "lICk9jZnE0hDtjFyO2GMCMgFhkwu4Fu71Vt_P-Ke64Y",
    "public_key": "7ctgKgpBixd1SHgQLr_G2dsNVMaQhHNw7P0PN4RBbEo",
    "device_id": "35c2b491c97fbcfad4a46efdfae98e149e806c43ebcc31d9614ff5abb44cf89e",
    "device_token": "a6d22ee710ba48fa8dfebab503b0023e",
}


def _b64url_decode(value: str) -> bytes:
    padding = 4 - len(value) % 4
    if padding != 4:
        value += "=" * padding
    return base64.urlsafe_b64decode(value)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class OpenClawClient:
    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._event_handlers: list[Any] = []
        self._listen_task: asyncio.Task[Any] | None = None
        self._private_key = Ed25519PrivateKey.from_private_bytes(_b64url_decode(PAIRED_DEVICE["private_key"]))

    async def connect(self) -> None:
        origin = self.url.replace("ws://", "http://").replace("wss://", "https://")
        self.ws = await websockets.connect(self.url, additional_headers={"Origin": origin})
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        data = json.loads(raw)
        nonce = ""
        if data.get("type") == "event" and data.get("event") == "connect.challenge":
            nonce = data.get("payload", {}).get("nonce", "")

        client_id = "openq-demo"
        signed_at_ms = int(time.time() * 1000)
        scopes = ["operator.admin", "operator.approvals"]
        message = "|".join(
            [
                "v2",
                PAIRED_DEVICE["device_id"],
                client_id,
                "webchat",
                "operator",
                ",".join(scopes),
                str(signed_at_ms),
                PAIRED_DEVICE["device_token"],
                nonce,
            ]
        )
        signature = _b64url_encode(self._private_key.sign(message.encode("utf-8")))
        await self.request(
            "connect",
            {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {"id": client_id, "version": "dev", "platform": "linux", "mode": "webchat"},
                "role": "operator",
                "scopes": scopes,
                "device": {
                    "id": PAIRED_DEVICE["device_id"],
                    "publicKey": PAIRED_DEVICE["public_key"],
                    "signature": signature,
                    "signedAt": signed_at_ms,
                    "nonce": nonce,
                },
                "auth": {"token": PAIRED_DEVICE["device_token"]},
            },
        )
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        try:
            assert self.ws is not None
            async for raw in self.ws:
                data = json.loads(raw)
                if data.get("type") == "res":
                    future = self._pending.pop(data["id"], None)
                    if future and not future.done():
                        if data.get("ok"):
                            future.set_result(data.get("payload"))
                        else:
                            future.set_exception(RuntimeError(data.get("error", {}).get("message", "request failed")))
                elif data.get("type") == "event":
                    for handler in list(self._event_handlers):
                        handler(data)
        except websockets.exceptions.ConnectionClosed:
            return

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        assert self.ws is not None
        request_id = str(uuid.uuid4())
        await self.ws.send(json.dumps({"type": "req", "id": request_id, "method": method, "params": params}))
        if self._listen_task is None:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            data = json.loads(raw)
            if not data.get("ok"):
                raise RuntimeError(data.get("error", {}).get("message", "request failed"))
            return data.get("payload")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        return await asyncio.wait_for(future, timeout=90)

    async def chat_send(self, message: str, session_key: str) -> str:
        response_text = ""
        done = asyncio.Event()

        def handler(data: dict[str, Any]) -> None:
            nonlocal response_text
            payload = data.get("payload", {})
            if data.get("event") == "agent" and payload.get("stream") == "assistant":
                text = payload.get("data", {}).get("text", "")
                if len(text) > len(response_text):
                    response_text = text
            if data.get("event") == "chat" and payload.get("state") in {"final", "error", "aborted"}:
                done.set()

        self._event_handlers.append(handler)
        try:
            await self.request(
                "chat.send",
                {"sessionKey": session_key, "message": message, "deliver": True, "idempotencyKey": str(uuid.uuid4())},
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=45)
            except asyncio.TimeoutError:
                pass
            return response_text
        finally:
            if handler in self._event_handlers:
                self._event_handlers.remove(handler)

    async def close(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()


@dataclass
class OpenClawFacade:
    url: str

    async def generate_reply(self, scenario: ScenarioDefinition, mode: str, use_real_openclaw: bool) -> str:
        prompt = self._build_prompt(scenario, mode)
        if not use_real_openclaw:
            return self._local_reply(scenario, mode)
        try:
            client = OpenClawClient(self.url)
            await client.connect()
            try:
                reply = await client.chat_send(prompt, session_key=f"openq-{scenario.scenario_id}")
                return reply or self._local_reply(scenario, mode)
            finally:
                await client.close()
        except Exception:
            return self._local_reply(scenario, mode)

    def _build_prompt(self, scenario: ScenarioDefinition, mode: str) -> str:
        return (
            "你是 OpenQ 演示里的 OpenClaw。请根据以下任务给出一段简短说明，不要编造工具执行结果。\n"
            f"模式: {mode}\n任务: {scenario.user_task}\n场景说明: {scenario.description}\n"
        )

    def _local_reply(self, scenario: ScenarioDefinition, mode: str) -> str:
        return f"OpenClaw 已接收任务：{scenario.user_task}。当前运行模式为 {mode}，将通过统一网关执行结构化调用。"

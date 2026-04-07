"""
OpenClaw WebSocket 客户端
========================
封装与 OpenClaw Gateway 的 WebSocket 通信逻辑。
协议：基于 JSON 的 RPC over WebSocket，使用 Ed25519 设备身份签名。
使用已配对的设备身份（从 Chrome localStorage 提取）进行认证。
"""

import asyncio
import base64
import hashlib
import json
import time
import uuid

import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# 已配对的设备身份（从 Chrome localStorage 中提取）
_PAIRED_DEVICE = {
    "private_key": "lICk9jZnE0hDtjFyO2GMCMgFhkwu4Fu71Vt_P-Ke64Y",
    "public_key": "7ctgKgpBixd1SHgQLr_G2dsNVMaQhHNw7P0PN4RBbEo",
    "device_id": "35c2b491c97fbcfad4a46efdfae98e149e806c43ebcc31d9614ff5abb44cf89e",
    "device_token": "a6d22ee710ba48fa8dfebab503b0023e",
}


class OpenClawClient:
    """与 OpenClaw Gateway 进行 WebSocket RPC 通信的客户端。"""

    def __init__(self, url="ws://localhost:18789"):
        self.url = url
        self.ws = None
        self._pending = {}  # id -> Future
        self._event_handlers = []
        self._listen_task = None
        # 加载已配对的设备身份
        self._priv_key = Ed25519PrivateKey.from_private_bytes(
            _b64url_decode(_PAIRED_DEVICE["private_key"]))
        self._device_id = _PAIRED_DEVICE["device_id"]
        self._pub_key_b64 = _PAIRED_DEVICE["public_key"]
        self._device_token = _PAIRED_DEVICE["device_token"]

    async def connect(self):
        """建立 WebSocket 连接并完成握手。"""
        origin = self.url.replace("ws://", "http://").replace("wss://", "https://")
        self.ws = await websockets.connect(self.url, additional_headers={"Origin": origin})

        # 1. 等待 connect.challenge 事件
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        data = json.loads(raw)
        nonce = None
        if data.get("type") == "event" and data.get("event") == "connect.challenge":
            nonce = data.get("payload", {}).get("nonce")

        # 2. 构建设备身份签名
        client_id = "openclaw-control-ui"
        client_mode = "webchat"
        role = "operator"
        scopes = ["operator.admin", "operator.approvals", "operator.pairing"]
        signed_at_ms = int(time.time() * 1000)

        # 签名消息: "v2|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce"
        parts = ["v2", self._device_id, client_id, client_mode, role,
                 ",".join(scopes), str(signed_at_ms), self._device_token,
                 nonce or ""]
        message_to_sign = "|".join(parts)
        signature = _b64url_encode(self._priv_key.sign(message_to_sign.encode("utf-8")))

        device_info = {
            "id": self._device_id,
            "publicKey": self._pub_key_b64,
            "signature": signature,
            "signedAt": signed_at_ms,
            "nonce": nonce,
        }

        # 3. 发送 connect 请求
        connect_params = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": client_id,
                "version": "dev",
                "platform": "windows",
                "mode": client_mode,
            },
            "role": role,
            "scopes": scopes,
            "device": device_info,
            "caps": [],
            "auth": {"token": self._device_token},
            "userAgent": "Python/OpenClawClient",
            "locale": "zh-CN",
        }

        hello = await self.request("connect", connect_params)

        # 4. 启动后台消息监听
        self._listen_task = asyncio.create_task(self._listen_loop())

        return hello

    async def _listen_loop(self):
        """持续监听 WebSocket 消息，分发到 pending requests 或 event handlers。"""
        try:
            async for raw in self.ws:
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "res":
                    msg_id = data.get("id")
                    future = self._pending.pop(msg_id, None)
                    if future and not future.done():
                        if data.get("ok"):
                            future.set_result(data.get("payload"))
                        else:
                            future.set_exception(
                                Exception(data.get("error", {}).get("message", "request failed"))
                            )
                elif msg_type == "event":
                    for handler in self._event_handlers:
                        try:
                            handler(data)
                        except Exception:
                            pass
        except websockets.exceptions.ConnectionClosed:
            pass

    async def request(self, method, params=None):
        """发送 RPC 请求并等待响应。"""
        msg_id = str(uuid.uuid4())
        msg = {"type": "req", "id": msg_id, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(msg))

        # 如果后台监听还没启动（connect 阶段），手动等待响应
        if self._listen_task is None:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            data = json.loads(raw)
            if data.get("ok"):
                return data.get("payload")
            else:
                raise Exception(data.get("error", {}).get("message", "request failed"))
        else:
            # 后台已在监听，用 Future 等待
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[msg_id] = future
            return await asyncio.wait_for(future, timeout=120)

    def on_event(self, handler):
        """注册事件处理函数。"""
        self._event_handlers.append(handler)

    async def chat_send(self, message, session_key="main"):
        """
        向 OpenClaw 发送聊天消息并等待完整响应。
        使用流式事件 + 轮询 chat.history 的混合策略确保可靠获取回复。
        返回助手的完整回复文本。
        """
        idempotency_key = str(uuid.uuid4())
        response_text = ""
        done_event = asyncio.Event()

        def on_chat_event(data):
            nonlocal response_text
            if data.get("type") != "event":
                return
            payload = data.get("payload", {})

            if data.get("event") == "chat":
                state = payload.get("state")
                msg = payload.get("message", {})
                if isinstance(msg, dict):
                    for part in msg.get("content", []):
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if len(text) > len(response_text):
                                response_text = text
                if state in ("final", "error", "aborted"):
                    done_event.set()

            elif data.get("event") == "agent":
                stream = payload.get("stream")
                dd = payload.get("data", {})
                if stream == "assistant":
                    text = dd.get("text", "")
                    if len(text) > len(response_text):
                        response_text = text
                elif stream == "lifecycle" and dd.get("phase") == "end":
                    done_event.set()

        self.on_event(on_chat_event)

        try:
            await self.request("chat.send", {
                "sessionKey": session_key,
                "message": message,
                "deliver": True,
                "idempotencyKey": idempotency_key,
            })

            # 等待流式事件完成（最多120秒）
            try:
                await asyncio.wait_for(done_event.wait(), timeout=120)
            except asyncio.TimeoutError:
                pass  # 流式超时，回退到轮询

            # 如果流式没获取到文本，轮询 chat.history
            if not response_text:
                for _ in range(30):  # 最多再等 150 秒
                    await asyncio.sleep(5)
                    try:
                        history = await self.request("chat.history", {
                            "sessionKey": session_key, "limit": 3,
                        })
                        messages = history.get("messages", [])
                        for m in reversed(messages):
                            if m.get("role") == "assistant":
                                for c in m.get("content", []):
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        response_text = c.get("text", "")
                                        if response_text:
                                            return response_text
                    except Exception:
                        pass

            return response_text
        finally:
            if on_chat_event in self._event_handlers:
                self._event_handlers.remove(on_chat_event)

    async def chat_history(self, session_key="main", limit=50):
        """获取聊天历史。"""
        return await self.request("chat.history", {
            "sessionKey": session_key,
            "limit": limit,
        })

    async def close(self):
        """关闭连接。"""
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()

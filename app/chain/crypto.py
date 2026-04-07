from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.utils import b64decode, b64encode, canonical_json, sha256_json


@dataclass
class Identity:
    did: str
    private_key: str
    public_key: str
    label: str


class RequestSigner:
    @staticmethod
    def generate_identity(did: str, label: str) -> Identity:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return Identity(
            did=did,
            private_key=b64encode(private_bytes),
            public_key=b64encode(public_bytes),
            label=label,
        )

    @staticmethod
    def payload_hash(payload: dict[str, Any]) -> str:
        return sha256_json(payload)

    @staticmethod
    def sign_payload(payload: dict[str, Any], private_key_b64: str) -> str:
        private_key = Ed25519PrivateKey.from_private_bytes(b64decode(private_key_b64))
        return b64encode(private_key.sign(canonical_json(payload)))

    @staticmethod
    def verify_payload(payload: dict[str, Any], signature_b64: str, public_key_b64: str) -> bool:
        try:
            public_key = Ed25519PublicKey.from_public_bytes(b64decode(public_key_b64))
            public_key.verify(b64decode(signature_b64), canonical_json(payload))
            return True
        except Exception:
            return False

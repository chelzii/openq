from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DASHSCOPE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "dashscope.yaml"
ENV_DASHSCOPE_API_KEY = "DASHSCOPE_API_KEY"
ENV_DASHSCOPE_CONFIG_PATH = "DASHSCOPE_CONFIG_PATH"


def _resolve_config_path(config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    override = os.getenv(ENV_DASHSCOPE_CONFIG_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_DASHSCOPE_CONFIG_PATH


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _extract_api_key(data: dict[str, Any]) -> str:
    for key in ("api_key", "apiKey", "apikey", "dashscope_api_key", "DASHSCOPE_API_KEY"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    nested = data.get("dashscope")
    if isinstance(nested, dict):
        for key in ("api_key", "apiKey", "apikey"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _extract_base_url(data: dict[str, Any]) -> str:
    value = data.get("base_url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    nested = data.get("dashscope")
    if isinstance(nested, dict):
        value = nested.get("base_url")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "https://dashscope.aliyuncs.com/api/v1"


@dataclass(frozen=True)
class DashScopeConfig:
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api/v1"

    @classmethod
    def load(cls, config_path: Path | None = None) -> "DashScopeConfig":
        path = _resolve_config_path(config_path)
        data = _load_yaml_mapping(path)
        api_key = _extract_api_key(data)
        if not api_key:
            api_key = os.getenv(ENV_DASHSCOPE_API_KEY, "").strip()
        return cls(
            api_key=api_key,
            base_url=_extract_base_url(data),
        )


def get_dashscope_api_key(config_path: Path | None = None) -> str | None:
    api_key = DashScopeConfig.load(config_path).api_key.strip()
    return api_key or None

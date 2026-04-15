import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.dashscope import DashScopeConfig, get_dashscope_api_key


class DashScopeConfigTests(unittest.TestCase):
    def test_reads_api_key_from_yaml_before_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "dashscope.yaml"
            config_path.write_text(
                'api_key: "yaml-key"\nbase_url: "https://example.invalid"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=False):
                config = DashScopeConfig.load(config_path)

        self.assertEqual(config.api_key, "yaml-key")
        self.assertEqual(config.base_url, "https://example.invalid")

    def test_falls_back_to_env_when_yaml_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "dashscope.yaml"
            config_path.write_text("api_key: \"\"\n", encoding="utf-8")
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=False):
                api_key = get_dashscope_api_key(config_path)

        self.assertEqual(api_key, "env-key")

    def test_missing_yaml_uses_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "missing.yaml"
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=False):
                config = DashScopeConfig.load(config_path)

        self.assertEqual(config.api_key, "env-key")

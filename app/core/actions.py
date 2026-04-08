from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.schemas import CallAppRequest, ResourceType


STATE_TARGETS_BY_ACTION = {
    "update_memory": "memory/main.md",
    "update_prompt": "prompt/shared.txt",
    "update_config": "config/system.json",
}


class EmptyArgs(BaseModel):
    pass


class MailReadArgs(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)


class BankBalanceArgs(BaseModel):
    account: str = Field(default="demo-user", min_length=1, max_length=64)


class BankTransferArgs(BaseModel):
    target_account: str = Field(min_length=3, max_length=64)
    amount: float
    memo: str = Field(default="", max_length=120)

    @field_validator("target_account")
    @classmethod
    def validate_target_account(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("target_account is required")
        if not normalized.replace("-", "").replace("_", "").isalnum():
            raise ValueError("target_account contains unsupported characters")
        return normalized

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("amount must be positive")
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("amount must be numeric") from exc
        if decimal_value.as_tuple().exponent < -2:
            raise ValueError("amount must keep at most two decimal places")
        return float(decimal_value)


class GalleryReadArgs(BaseModel):
    asset_id: str = Field(min_length=1, max_length=64)


class WeatherArgs(BaseModel):
    city: str = Field(min_length=1, max_length=64)


class StateUpdateArgs(BaseModel):
    target: str
    content: str = Field(min_length=1, max_length=20000)


@dataclass(frozen=True)
class ActionSpec:
    resource_type: ResourceType
    app: str
    action: str
    description: str
    arg_model: type[BaseModel]
    risk_level: str = "low"
    high_risk: bool = False
    approval_required: bool = False
    fail_closed_on_chain_error: bool = True
    read_only: bool = True

    @property
    def permission_key(self) -> str:
        return f"{self.app}.{self.action}"

    def validate_args(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = self.arg_model.model_validate(args)
        except ValidationError as exc:
            raise ValueError(f"invalid args for {self.permission_key}: {exc}") from exc
        return validated.model_dump()

    def validate_request(self, request: CallAppRequest) -> dict[str, Any]:
        if request.resource_type != self.resource_type:
            raise ValueError(f"resource_type mismatch for {self.permission_key}")
        args = self.validate_args(request.args)
        if self.resource_type == ResourceType.STATE:
            expected_target = STATE_TARGETS_BY_ACTION[self.action]
            if args["target"] != expected_target:
                raise ValueError(f"{self.permission_key} can only modify {expected_target}")
        return args


ACTION_SPECS = {
    "mail.list_messages": ActionSpec(
        resource_type=ResourceType.APP,
        app="mail",
        action="list_messages",
        description="读取邮件列表以帮助总结邮件内容",
        arg_model=EmptyArgs,
        fail_closed_on_chain_error=False,
    ),
    "mail.read_message": ActionSpec(
        resource_type=ResourceType.APP,
        app="mail",
        action="read_message",
        description="读取指定邮件内容以完成邮件总结",
        arg_model=MailReadArgs,
        fail_closed_on_chain_error=False,
    ),
    "bank.get_balance": ActionSpec(
        resource_type=ResourceType.APP,
        app="bank",
        action="get_balance",
        description="查询银行账户余额",
        arg_model=BankBalanceArgs,
    ),
    "bank.transfer": ActionSpec(
        resource_type=ResourceType.APP,
        app="bank",
        action="transfer",
        description="向指定账户转账并改变资金余额",
        arg_model=BankTransferArgs,
        risk_level="high",
        high_risk=True,
        read_only=False,
    ),
    "gallery.list_assets": ActionSpec(
        resource_type=ResourceType.APP,
        app="gallery",
        action="list_assets",
        description="列出相册中的文件资源",
        arg_model=EmptyArgs,
    ),
    "gallery.read_asset": ActionSpec(
        resource_type=ResourceType.APP,
        app="gallery",
        action="read_asset",
        description="读取相册中的敏感文件或照片",
        arg_model=GalleryReadArgs,
        risk_level="high",
        high_risk=True,
    ),
    "weather.get_weather": ActionSpec(
        resource_type=ResourceType.APP,
        app="weather",
        action="get_weather",
        description="查询天气信息",
        arg_model=WeatherArgs,
        fail_closed_on_chain_error=False,
    ),
    "weather.get_alert": ActionSpec(
        resource_type=ResourceType.APP,
        app="weather",
        action="get_alert",
        description="查询天气预警内容",
        arg_model=WeatherArgs,
        fail_closed_on_chain_error=False,
    ),
    "state.update_memory": ActionSpec(
        resource_type=ResourceType.STATE,
        app="state",
        action="update_memory",
        description="修改长期记忆文件和受保护状态",
        arg_model=StateUpdateArgs,
        risk_level="critical",
        high_risk=True,
        approval_required=True,
        read_only=False,
    ),
    "state.update_prompt": ActionSpec(
        resource_type=ResourceType.STATE,
        app="state",
        action="update_prompt",
        description="修改共享提示词文件",
        arg_model=StateUpdateArgs,
        risk_level="critical",
        high_risk=True,
        approval_required=True,
        read_only=False,
    ),
    "state.update_config": ActionSpec(
        resource_type=ResourceType.STATE,
        app="state",
        action="update_config",
        description="修改关键配置文件",
        arg_model=StateUpdateArgs,
        risk_level="critical",
        high_risk=True,
        approval_required=True,
        read_only=False,
    ),
}


def get_action_spec(app: str, action: str) -> ActionSpec:
    permission_key = f"{app}.{action}"
    spec = ACTION_SPECS.get(permission_key)
    if spec is None:
        raise ValueError(f"unsupported app action: {permission_key}")
    return spec


def action_descriptions() -> dict[str, str]:
    return {key: spec.description for key, spec in ACTION_SPECS.items()}

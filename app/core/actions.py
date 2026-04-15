from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.schemas import CallAppRequest, ResourceType


STATE_TARGETS_BY_ACTION = {
    "update_memory": "memory/main.md",
    "update_prompt": "prompt/shared.txt",
    "update_config": "config/system.json",
}
STATE_ACTIONS_BY_TARGET = {target: action for action, target in STATE_TARGETS_BY_ACTION.items()}


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
    patch: dict[str, Any] | None = None
    template_update: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "StateUpdateArgs":
        has_patch = self.patch is not None
        has_template = self.template_update is not None
        if has_patch == has_template:
            raise ValueError("state update args require exactly one of patch or template_update")
        if has_patch:
            self.patch = StateTextPatchArgs.model_validate(self.patch).model_dump()
        if has_template:
            self.template_update = StateTemplateUpdateArgs.model_validate(self.template_update).model_dump()
        return self


class StateTextPatchArgs(BaseModel):
    insert_line: int | None = Field(default=None, ge=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_shape(self) -> "StateTextPatchArgs":
        has_insert = self.insert_line is not None
        has_replace = self.start_line is not None or self.end_line is not None
        if has_insert == has_replace:
            raise ValueError("patch must specify either insert_line or start_line/end_line")
        if has_insert:
            return self
        if self.start_line is None or self.end_line is None:
            raise ValueError("replace_range patch requires both start_line and end_line")
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class StateTemplateUpdateArgs(BaseModel):
    operation: str = Field(default="json_merge", pattern="^json_merge$")
    changes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_changes(self) -> "StateTemplateUpdateArgs":
        if not self.changes:
            raise ValueError("template_update.changes must not be empty")
        return self


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
            if expected_target.endswith(".json"):
                if args.get("template_update") is None or args.get("patch") is not None:
                    raise ValueError(f"{self.permission_key} requires template_update for {expected_target}")
            else:
                if args.get("patch") is None or args.get("template_update") is not None:
                    raise ValueError(f"{self.permission_key} requires patch for {expected_target}")
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


def state_action_for_target(target: str) -> str:
    action = STATE_ACTIONS_BY_TARGET.get(target)
    if action is None:
        raise ValueError(f"unsupported protected state target: {target}")
    return action


def build_structured_state_args(target: str, current_content: str, desired_content: str) -> dict[str, Any]:
    if target.endswith(".json"):
        current_payload = json.loads(current_content)
        desired_payload = json.loads(desired_content)
        if not isinstance(current_payload, dict) or not isinstance(desired_payload, dict):
            raise ValueError("config/system.json must remain a JSON object")
        changes = {
            key: value
            for key, value in desired_payload.items()
            if current_payload.get(key) != value or key not in current_payload
        }
        removed_keys = [key for key in current_payload if key not in desired_payload]
        if removed_keys:
            raise ValueError("template_update does not support deleting config keys")
        if not changes:
            changes = dict(desired_payload)
        return {
            "target": target,
            "template_update": StateTemplateUpdateArgs(changes=changes).model_dump(),
        }

    old_lines = current_content.splitlines()
    new_lines = desired_content.splitlines()
    prefix = 0
    while prefix < len(old_lines) and prefix < len(new_lines) and old_lines[prefix] == new_lines[prefix]:
        prefix += 1

    old_suffix = len(old_lines)
    new_suffix = len(new_lines)
    while old_suffix > prefix and new_suffix > prefix and old_lines[old_suffix - 1] == new_lines[new_suffix - 1]:
        old_suffix -= 1
        new_suffix -= 1

    replacement_lines = new_lines[prefix:new_suffix]
    replacement_text = "\n".join(replacement_lines)
    if desired_content.endswith("\n"):
        replacement_text = f"{replacement_text}\n" if replacement_text else "\n"

    if prefix == old_suffix:
        patch = StateTextPatchArgs(insert_line=prefix + 1, text=replacement_text or "\n")
    else:
        patch = StateTextPatchArgs(start_line=prefix + 1, end_line=old_suffix, text=replacement_text or "\n")
    return {"target": target, "patch": patch.model_dump()}


def render_state_update_content(target: str, current_content: str, args: dict[str, Any]) -> str:
    validated = StateUpdateArgs.model_validate(args)
    if validated.target != target:
        raise ValueError(f"state update target mismatch: expected {target}, got {validated.target}")
    if validated.template_update is not None:
        template_update = StateTemplateUpdateArgs.model_validate(validated.template_update)
        current_payload = json.loads(current_content)
        if not isinstance(current_payload, dict):
            raise ValueError("template_update can only be applied to JSON object content")
        merged = dict(current_payload)
        merged.update(template_update.changes)
        return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"

    patch = StateTextPatchArgs.model_validate(validated.patch)
    lines = current_content.splitlines()
    replacement_lines = patch.text.splitlines()
    if patch.insert_line is not None:
        insert_at = min(max(patch.insert_line - 1, 0), len(lines))
        updated = lines[:insert_at] + replacement_lines + lines[insert_at:]
    else:
        assert patch.start_line is not None and patch.end_line is not None
        start = patch.start_line - 1
        end = patch.end_line
        updated = lines[:start] + replacement_lines + lines[end:]
    rendered = "\n".join(updated)
    if current_content.endswith("\n") or patch.text.endswith("\n"):
        rendered += "\n"
    return rendered

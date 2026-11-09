"""P3：把模型原始响应严格解析为唯一的 ``ModelDecision``。

输出协议（一次响应只能有一个决策）::

    {"kind": "final", "thought_summary": "简短判断", "answer": "最终回答"}
    {"kind": "tool_call", "thought_summary": "简短判断",
     "tool_call": {"name": "calculator", "arguments": {"expression": "1+1"}}}

约定：

- 只接受一个完整的 JSON 对象；空响应、前后缀文字、Markdown 代码块都视为非法，
  绝不把 malformed 输出降级当成 ``final``；
- 缺少 ``kind`` / ``answer`` / ``tool_call`` / ``name`` / ``arguments`` 等必需字段，
  或字段类型错误、未知决策类型，统一抛出 ``ParseError``；
- 传入 ``registry`` 时额外校验工具名与参数 Schema：未知工具抛 ``ParseError``，
  参数不符合 Schema 抛 ``ToolArgumentError``，两者都带可断言的结构化细节；
- 模型未提供 ``call_id`` 时由解析器生成，保证同一响应内工具调用与结果可对应。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .errors import ParseError, ToolArgumentError, ToolNotFoundError
from .models import ModelDecision, ToolCall
from .tools import ToolRegistry, validate_arguments

DECISION_KINDS = ("tool_call", "final")
_MAX_DETAIL_TEXT = 300
_THOUGHT_FIELD = "thought_summary"
_KIND_FIELD = "kind"
_TOOL_CALL_FIELD = "tool_call"
_ANSWER_FIELD = "answer"


def _truncate(text: str) -> str:
    if len(text) <= _MAX_DETAIL_TEXT:
        return text
    return text[: _MAX_DETAIL_TEXT - 1] + "…"


def _fail(message: str, reason: str, **details: Any) -> ParseError:
    return ParseError(message, {"reason": reason, **details})


def _parse_thought(payload: dict[str, Any]) -> str | None:
    if _THOUGHT_FIELD not in payload:
        return None
    value = payload[_THOUGHT_FIELD]
    if not isinstance(value, str):
        raise _fail(
            "thought_summary 必须是字符串",
            "invalid_field",
            field=_THOUGHT_FIELD,
            got=type(value).__name__,
        )
    return value


def _require_text(
    payload: dict[str, Any],
    field: str,
    *,
    container: str = "响应",
) -> str:
    if field not in payload:
        raise _fail(
            f"{container}缺少必需字段 {field!r}",
            "missing_field",
            field=field,
        )
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise _fail(
            f"{container}字段 {field!r} 必须是非空字符串",
            "invalid_field",
            field=field,
            got=type(value).__name__,
        )
    return value.strip()


def _parse_final(
    payload: dict[str, Any],
    thought_summary: str | None,
) -> ModelDecision:
    if _TOOL_CALL_FIELD in payload:
        raise _fail(
            "final 决策不能同时携带 tool_call",
            "conflicting_fields",
            fields=[_ANSWER_FIELD, _TOOL_CALL_FIELD],
        )
    answer = _require_text(payload, _ANSWER_FIELD)
    return ModelDecision.from_final(answer, thought_summary=thought_summary)


def _parse_tool_name(tool_call: dict[str, Any]) -> str:
    if "name" not in tool_call:
        raise _fail(
            "tool_call 缺少必需字段 'name'",
            "missing_field",
            field="tool_call.name",
        )
    name = tool_call["name"]
    if not isinstance(name, str) or not name.strip():
        raise _fail(
            "tool_call.name 必须是非空字符串",
            "invalid_field",
            field="tool_call.name",
            got=type(name).__name__,
        )
    return name.strip()


def _parse_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    if "arguments" not in tool_call:
        raise _fail(
            "tool_call 缺少必需字段 'arguments'",
            "missing_field",
            field="tool_call.arguments",
        )
    arguments = tool_call["arguments"]
    if not isinstance(arguments, dict):
        raise _fail(
            "tool_call.arguments 必须是 JSON 对象",
            "invalid_field",
            field="tool_call.arguments",
            got=type(arguments).__name__,
        )
    if any(not isinstance(key, str) for key in arguments):
        raise _fail(
            "tool_call.arguments 的键必须是字符串",
            "invalid_field",
            field="tool_call.arguments",
        )
    return dict(arguments)


def _parse_call_id(tool_call: dict[str, Any]) -> str:
    if "call_id" not in tool_call:
        return f"call_{uuid.uuid4().hex}"
    call_id = tool_call["call_id"]
    if not isinstance(call_id, str) or not call_id.strip():
        raise _fail(
            "tool_call.call_id 必须是非空字符串",
            "invalid_field",
            field="tool_call.call_id",
            got=type(call_id).__name__,
        )
    return call_id.strip()


def _validate_against_registry(call: ToolCall, registry: ToolRegistry) -> None:
    try:
        spec = registry.get(call.name)
    except ToolNotFoundError:
        raise _fail(
            f"模型选择了未注册的工具 {call.name!r}",
            "unknown_tool",
            tool=call.name,
        ) from None

    issues = validate_arguments(spec.parameters, call.arguments)
    if issues:
        raise ToolArgumentError(
            f"工具 {call.name!r} 的参数不符合 Schema",
            {"tool": call.name, "errors": issues},
        )


def _parse_tool_call(
    payload: dict[str, Any],
    thought_summary: str | None,
    registry: ToolRegistry | None,
) -> ModelDecision:
    if _ANSWER_FIELD in payload:
        raise _fail(
            "tool_call 决策不能同时携带 answer",
            "conflicting_fields",
            fields=[_TOOL_CALL_FIELD, _ANSWER_FIELD],
        )
    if _TOOL_CALL_FIELD not in payload:
        raise _fail(
            "tool_call 决策缺少必需字段 'tool_call'",
            "missing_field",
            field=_TOOL_CALL_FIELD,
        )
    tool_call = payload[_TOOL_CALL_FIELD]
    if not isinstance(tool_call, dict):
        raise _fail(
            "tool_call 必须是 JSON 对象",
            "invalid_field",
            field=_TOOL_CALL_FIELD,
            got=type(tool_call).__name__,
        )

    call = ToolCall(
        call_id=_parse_call_id(tool_call),
        name=_parse_tool_name(tool_call),
        arguments=_parse_arguments(tool_call),
    )
    if registry is not None:
        _validate_against_registry(call, registry)
    return ModelDecision.from_tool_call(call, thought_summary=thought_summary)


def parse_decision(
    raw: str,
    *,
    registry: ToolRegistry | None = None,
) -> ModelDecision:
    """解析一次模型响应。

    ``raw`` 必须是单个 JSON 对象的文本；传入 ``registry`` 时同时校验工具名与参数。
    """
    if not isinstance(raw, str):
        raise ValueError("raw response must be a string")
    if registry is not None and not isinstance(registry, ToolRegistry):
        raise ValueError("registry must be a ToolRegistry or None")

    text = raw.strip()
    if not text:
        raise _fail("模型响应为空", "empty_response")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(
            "模型响应不是合法 JSON",
            "invalid_json",
            line=exc.lineno,
            column=exc.colno,
            response=_truncate(text),
        ) from exc

    if not isinstance(payload, dict):
        raise _fail(
            "模型响应必须是 JSON 对象",
            "not_an_object",
            got=type(payload).__name__,
        )

    if _KIND_FIELD not in payload:
        raise _fail(
            "响应缺少必需字段 'kind'",
            "missing_field",
            field=_KIND_FIELD,
        )

    kind = payload[_KIND_FIELD]
    if not isinstance(kind, str) or kind not in DECISION_KINDS:
        raise _fail(
            f"未知的决策类型：{kind!r}",
            "unknown_kind",
            field=_KIND_FIELD,
            allowed=list(DECISION_KINDS),
            got=kind,
        )

    thought_summary = _parse_thought(payload)
    if kind == "final":
        return _parse_final(payload, thought_summary)
    return _parse_tool_call(payload, thought_summary, registry)


__all__ = ["DECISION_KINDS", "parse_decision"]

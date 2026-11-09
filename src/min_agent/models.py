"""P0 统一数据契约：消息、完整轮次、工具调用/结果、模型决策。

这些类型会被 Session、Context、Tool Registry 和 Loop 共同使用，因此：
- 值对象使用 frozen dataclass，避免跨模块被意外改写；
- 构造时校验关键不变量，非法状态尽早失败；
- 运行期失败用 ``ErrorInfo`` 表达，不把异常对象塞进数据里；
- ``thought_summary`` 只是简短摘要，不承载模型隐藏推理链。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .errors import ErrorInfo


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class DecisionKind(StrEnum):
    TOOL_CALL = "tool_call"
    FINAL = "final"


class TurnStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def _as_enum(value: Any, enum_type: type[StrEnum], field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(
            f"{field_name} must be one of: {allowed}; got {value!r}"
        ) from None


def _as_text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _as_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    text = _as_text(value, field_name, allow_empty=True).strip()
    return text or None


def _as_tool_calls(value: Any, role: Role) -> tuple[ToolCall, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise ValueError("tool_calls must be a sequence of ToolCall")
    calls = tuple(value)
    for call in calls:
        if not isinstance(call, ToolCall):
            raise ValueError("tool_calls must contain only ToolCall objects")
    if calls and role is not Role.ASSISTANT:
        raise ValueError("only assistant messages may carry tool_calls")
    return calls


@dataclass(frozen=True, slots=True)
class Message:
    """写入 Context 的一条消息。

    assistant 消息可携带 ``tool_calls``，用于在历史中保留模型发起过的工具调用；
    tool 消息通过 ``tool_call_id`` 与之对应。工具调用和结果成对出现，不存半轮。
    """

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _as_enum(self.role, Role, "role"))
        _as_text(self.content, "content", allow_empty=True)
        if self.name is not None:
            _as_text(self.name, "name")
        if self.role is Role.TOOL:
            if not self.tool_call_id or not isinstance(self.tool_call_id, str):
                raise ValueError("tool message requires a non-empty tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("only tool messages may carry tool_call_id")
        object.__setattr__(
            self, "tool_calls", _as_tool_calls(self.tool_calls, self.role)
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求执行的一次工具调用。"""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _as_text(self.call_id, "call_id")
        object.__setattr__(self, "name", _as_text(self.name, "name").strip())
        if not isinstance(self.arguments, dict):
            raise ValueError("arguments must be a dict")
        if any(not isinstance(key, str) for key in self.arguments):
            raise ValueError("argument keys must be strings")
        object.__setattr__(self, "arguments", dict(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行的统一结果；成功与失败由 ``ok`` 明确区分。"""

    call_id: str
    name: str
    ok: bool
    output: Any = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        _as_text(self.call_id, "call_id")
        object.__setattr__(self, "name", _as_text(self.name, "name").strip())
        if not isinstance(self.ok, bool):
            raise ValueError("ok must be a bool")
        if self.ok:
            if self.error is not None:
                raise ValueError("successful tool result must not carry an error")
        else:
            if not isinstance(self.error, ErrorInfo):
                raise ValueError("failed tool result must carry an ErrorInfo")
            if self.output is not None:
                raise ValueError("failed tool result must not carry output")

    @classmethod
    def success(
        cls,
        call_id: str,
        name: str,
        output: Any = None,
    ) -> ToolResult:
        return cls(call_id=call_id, name=name, ok=True, output=output)

    @classmethod
    def failure(
        cls,
        call_id: str,
        name: str,
        error: ErrorInfo,
    ) -> ToolResult:
        return cls(call_id=call_id, name=name, ok=False, error=error)

    @property
    def error_code(self) -> str | None:
        return self.error.code if self.error is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "ok": self.ok,
            "output": self.output,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ModelDecision:
    """一次模型响应的唯一决策：``tool_call`` 或 ``final``。"""

    kind: DecisionKind
    thought_summary: str | None = None
    tool_call: ToolCall | None = None
    answer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", _as_enum(self.kind, DecisionKind, "decision kind")
        )
        object.__setattr__(
            self,
            "thought_summary",
            _as_optional_text(self.thought_summary, "thought_summary"),
        )
        if self.kind is DecisionKind.TOOL_CALL:
            if not isinstance(self.tool_call, ToolCall):
                raise ValueError("tool_call decision requires a ToolCall")
            if self.answer is not None:
                raise ValueError("tool_call decision must not carry an answer")
        else:
            _as_text(self.answer, "answer")
            if self.tool_call is not None:
                raise ValueError("final decision must not carry a tool_call")

    @classmethod
    def from_tool_call(
        cls,
        tool_call: ToolCall,
        thought_summary: str | None = None,
    ) -> ModelDecision:
        return cls(
            kind=DecisionKind.TOOL_CALL,
            thought_summary=thought_summary,
            tool_call=tool_call,
        )

    @classmethod
    def from_final(
        cls,
        answer: str,
        thought_summary: str | None = None,
    ) -> ModelDecision:
        return cls(
            kind=DecisionKind.FINAL,
            thought_summary=thought_summary,
            answer=answer,
        )

    @property
    def is_tool_call(self) -> bool:
        return self.kind is DecisionKind.TOOL_CALL

    @property
    def is_final(self) -> bool:
        return self.kind is DecisionKind.FINAL


@dataclass(frozen=True, slots=True)
class ToolExchange:
    """轮次内的一次“工具调用 + 工具结果”，二者不可拆分。"""

    tool_call: ToolCall
    tool_result: ToolResult
    thought_summary: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call, ToolCall):
            raise ValueError("tool_call must be a ToolCall")
        if not isinstance(self.tool_result, ToolResult):
            raise ValueError("tool_result must be a ToolResult")
        if self.tool_call.call_id != self.tool_result.call_id:
            raise ValueError("tool_result.call_id must match tool_call.call_id")
        if self.tool_call.name != self.tool_result.name:
            raise ValueError("tool_result.name must match tool_call.name")
        object.__setattr__(
            self,
            "thought_summary",
            _as_optional_text(self.thought_summary, "thought_summary"),
        )


@dataclass(slots=True)
class Turn:
    """一个完整对话轮次：用户输入、若干工具调用及其结果、最终答案。

    轮次一旦结束（completed/failed）不可再追加工具调用，避免半轮历史。
    """

    session_id: str
    user_input: str
    turn_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)
    tool_exchanges: list[ToolExchange] = field(default_factory=list)
    answer: str | None = None
    status: TurnStatus = TurnStatus.OPEN
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        _as_text(self.session_id, "session_id")
        _as_text(self.user_input, "user_input")
        _as_text(self.turn_id, "turn_id")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be a timezone-aware datetime")
        object.__setattr__(self, "status", _as_enum(self.status, TurnStatus, "status"))
        if self.answer is not None or self.error is not None:
            raise ValueError("a new turn must not carry an answer or error")
        if self.status is not TurnStatus.OPEN:
            raise ValueError("a new turn must start as open")

    @property
    def is_open(self) -> bool:
        return self.status is TurnStatus.OPEN

    @property
    def is_completed(self) -> bool:
        return self.status is TurnStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status is TurnStatus.FAILED

    def add_exchange(
        self,
        tool_call: ToolCall,
        tool_result: ToolResult,
        thought_summary: str | None = None,
    ) -> ToolExchange:
        """追加一次工具调用及结果；仅开放中的轮次可写。"""
        if not self.is_open:
            raise ValueError("cannot add tool exchange to a finished turn")
        exchange = ToolExchange(
            tool_call=tool_call,
            tool_result=tool_result,
            thought_summary=thought_summary,
        )
        self.tool_exchanges.append(exchange)
        return exchange

    def complete(self, answer: str) -> None:
        if not self.is_open:
            raise ValueError("cannot complete a finished turn")
        _as_text(answer, "answer")
        self.answer = answer
        self.status = TurnStatus.COMPLETED

    def fail(self, error: ErrorInfo) -> None:
        if not self.is_open:
            raise ValueError("cannot fail a finished turn")
        if not isinstance(error, ErrorInfo):
            raise ValueError("error must be an ErrorInfo")
        self.error = error
        self.status = TurnStatus.FAILED

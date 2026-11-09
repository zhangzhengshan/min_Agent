"""结构化错误：统一运行期错误的分类、载荷与序列化形式。

约定：
- ``ErrorInfo`` 是跨模块传递的纯数据错误载荷，可放入 ``ToolResult`` 或 ``Turn``；
- ``AgentError`` 及其子类是运行期异常，携带稳定的 ``code``，供 Loop 与 CLI 区分处理；
- 构造数据对象时违反类型/状态约束属于编程错误，直接抛 ``ValueError`` / ``TypeError``，
  不使用这里的运行期错误类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ErrorCode(StrEnum):
    """稳定的错误码；新增错误类型时在此登记。"""

    AGENT_ERROR = "agent_error"
    CONFIG_INVALID = "config_invalid"
    MODEL_REQUEST_FAILED = "model_request_failed"
    OUTPUT_PARSE_FAILED = "output_parse_failed"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    TOOL_NOT_FOUND = "tool_not_found"
    DUPLICATE_TOOL = "duplicate_tool"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_LIMIT_EXCEEDED = "tool_limit_exceeded"
    SUMMARIZER_FAILED = "summarizer_failed"
    SESSION_NOT_FOUND = "session_not_found"


def _code_value(code: ErrorCode | str) -> str:
    if isinstance(code, ErrorCode):
        return code.value
    if isinstance(code, str) and code.strip():
        return code
    raise ValueError("error code must be a non-empty string or ErrorCode")


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """可序列化的结构化错误。"""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("error message must be a non-empty string")
        if not isinstance(self.details, Mapping):
            raise ValueError("error details must be a mapping")
        object.__setattr__(self, "code", _code_value(self.code))
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class AgentError(Exception):
    """所有可预期运行期错误的基类。"""

    code: ErrorCode = ErrorCode.AGENT_ERROR

    def __init__(
        self,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("error message must be a non-empty string")
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, details=self.details)

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class ConfigError(AgentError):
    code = ErrorCode.CONFIG_INVALID


class ModelError(AgentError):
    """模型请求失败；本轮应终止并返回明确信息。"""

    code = ErrorCode.MODEL_REQUEST_FAILED


class ParseError(AgentError):
    """模型输出无法解析为合法决策。"""

    code = ErrorCode.OUTPUT_PARSE_FAILED


class ToolArgumentError(AgentError):
    """工具参数缺失、类型错误或不符合 Schema。"""

    code = ErrorCode.INVALID_TOOL_ARGUMENTS


class ToolNotFoundError(AgentError):
    code = ErrorCode.TOOL_NOT_FOUND


class DuplicateToolError(AgentError):
    code = ErrorCode.DUPLICATE_TOOL


class ToolExecutionError(AgentError):
    """工具处理函数内部失败。"""

    code = ErrorCode.TOOL_EXECUTION_FAILED


class ToolLimitError(AgentError):
    """单轮工具调用次数达到上限。"""

    code = ErrorCode.TOOL_LIMIT_EXCEEDED


class SummarizationError(AgentError):
    """滚动摘要生成失败；必须保留原始历史并终止本轮。"""

    code = ErrorCode.SUMMARIZER_FAILED


class SessionNotFoundError(AgentError):
    code = ErrorCode.SESSION_NOT_FOUND

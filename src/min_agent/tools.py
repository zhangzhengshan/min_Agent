"""P2：工具注册、参数校验与统一执行入口。

设计要点：

- ``ToolSpec`` 是工具的唯一描述：名称、描述、参数 JSON Schema 与处理函数；
- ``ToolRegistry`` 只负责登记与列出，重复注册显式失败，未知名称显式报错；
- 参数校验使用项目内置的 JSON Schema 子集，不引入第三方依赖；
- ``ToolExecutor`` 把未知工具、参数错误和处理函数异常统一转为 ``ToolResult``，
  调用方（P4 Loop）无需用 ``try/except`` 区分工具失败；
- 处理函数通过 ``ToolContext`` 取得当前 ``Session``，使 todo 等状态按会话隔离；
- 工具 Schema 只描述能力，不包含任何关键词路由逻辑。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import (
    AgentError,
    DuplicateToolError,
    ErrorCode,
    ErrorInfo,
    ToolExecutionError,
    ToolNotFoundError,
)
from .models import ToolCall, ToolResult
from .session import Session

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUPPORTED_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)
_MAX_ERROR_MESSAGE_LENGTH = 300

ToolHandler = Callable[[dict[str, Any], "ToolContext"], Any]


def _validate_tool_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("tool name must be a non-empty string")
    name = value.strip()
    if not _TOOL_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "tool name may only contain letters, digits, underscores and hyphens "
            "(max 64 chars)"
        )
    return name


def _validate_description(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("tool description must be a non-empty string")
    return value.strip()


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_schema_definition(schema: Any, path: str = "$") -> None:
    """校验本项目支持的 JSON Schema 子集，尽早暴露手写 Schema 的错误。"""
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {path} must be an object")

    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else expected
        if not isinstance(types, list) or not types:
            raise ValueError(f"schema type at {path} must be a string or list")
        unknown = [item for item in types if item not in _SUPPORTED_SCHEMA_TYPES]
        if unknown:
            raise ValueError(f"unsupported schema type at {path}: {unknown!r}")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ValueError(f"properties at {path} must be an object")
        for key, child in properties.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"property names at {path} must be non-empty strings")
            _validate_schema_definition(child, f"{path}.properties.{key}")

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise ValueError(f"required at {path} must be a list of strings")
        if not isinstance(properties, dict) or any(
            item not in properties for item in required
        ):
            raise ValueError(
                f"required at {path} must reference declared properties"
            )

    additional = schema.get("additionalProperties")
    if additional is not None:
        if isinstance(additional, bool):
            pass
        elif isinstance(additional, dict):
            _validate_schema_definition(additional, f"{path}.additionalProperties")
        else:
            raise ValueError(
                f"additionalProperties at {path} must be a bool or schema"
            )

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"enum at {path} must be a non-empty list")

    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise ValueError(f"items at {path} must be a schema object")
        _validate_schema_definition(items, f"{path}.items")

    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        value = schema.get(key)
        if value is not None and (not _is_integer(value) or value < 0):
            raise ValueError(f"{key} at {path} must be a non-negative integer")

    for key in ("minimum", "maximum"):
        value = schema.get(key)
        if value is not None and not _is_number(value):
            raise ValueError(f"{key} at {path} must be a number")


def _validate_parameters_schema(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        raise ValueError("tool parameters must be an object schema")
    schema = copy.deepcopy(dict(parameters))
    if schema.get("type") != "object":
        raise ValueError("tool parameters schema must declare type 'object'")
    _validate_schema_definition(schema)
    return schema


@dataclass(frozen=True, slots=True)
class ToolContext:
    """工具执行时的上下文；当前只承载 Session，后续扩展不改变处理函数签名。"""

    session: Session | None = None

    def __post_init__(self) -> None:
        if self.session is not None and not isinstance(self.session, Session):
            raise ValueError("session must be a Session or None")

    def require_session(self, tool_name: str) -> Session:
        """取得会话；无会话时抛出可由 Executor 转成统一结果的结构化错误。"""
        if self.session is None:
            raise ToolExecutionError(
                f"工具 {tool_name!r} 需要会话上下文",
                {"tool": tool_name, "reason": "missing_session"},
            )
        return self.session


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个工具的完整定义；``parameters`` 使用 JSON Schema 的 object 子集。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    requires_session: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _validate_tool_name(self.name))
        object.__setattr__(
            self, "description", _validate_description(self.description)
        )
        object.__setattr__(
            self, "parameters", _validate_parameters_schema(self.parameters)
        )
        if not callable(self.handler):
            raise ValueError("tool handler must be callable")
        if not isinstance(self.requires_session, bool):
            raise ValueError("requires_session must be a bool")

    def to_schema(self) -> dict[str, Any]:
        """返回可直接交给 LLM 的工具 Schema（不含处理函数）。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
        }


class ToolRegistry:
    """工具登记表：注册、查找、列出 Schema。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.strip() in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> tuple[str, ...]:
        """按注册顺序返回工具名。"""
        return tuple(self._specs)

    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册一个工具；同名工具已存在时抛出 ``DuplicateToolError``。"""
        if not isinstance(spec, ToolSpec):
            raise ValueError("spec must be a ToolSpec")
        if spec.name in self._specs:
            raise DuplicateToolError(
                f"工具 {spec.name!r} 已注册",
                {"tool": spec.name},
            )
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        """按名称取得工具；未注册时抛出 ``ToolNotFoundError``。"""
        key = name.strip() if isinstance(name, str) else name
        spec = self._specs.get(key) if isinstance(key, str) else None
        if spec is None:
            raise ToolNotFoundError(
                f"工具 {name!r} 未注册",
                {"tool": name},
            )
        return spec

    def schema_for(self, name: str) -> dict[str, Any]:
        return self.get(name).to_schema()

    def schemas(self) -> list[dict[str, Any]]:
        """列出全部工具的 Schema，供 P3 构造模型请求。"""
        return [spec.to_schema() for spec in self._specs.values()]


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_json_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return _is_integer(value)
    if type_name == "number":
        return _is_number(value)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    return False


def _child_path(path: str, name: str) -> str:
    return f"{path}.{name}"


def _validate_value(
    value: Any,
    schema: Mapping[str, Any],
    path: str,
    issues: list[dict[str, Any]],
) -> None:
    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_matches_json_type(value, item) for item in types):
            issues.append(
                {
                    "path": path,
                    "reason": "expected "
                    + " or ".join(types)
                    + f", got {_json_type_name(value)}",
                }
            )
            return

    enum = schema.get("enum")
    if enum is not None and not any(value == candidate for candidate in enum):
        issues.append(
            {
                "path": path,
                "reason": f"value is not in the allowed enum: {enum!r}",
            }
        )

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            issues.append(
                {
                    "path": path,
                    "reason": f"length must be at least {min_length}",
                }
            )
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            issues.append(
                {
                    "path": path,
                    "reason": f"length must be at most {max_length}",
                }
            )

    if _is_number(value):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            issues.append(
                {"path": path, "reason": f"must be greater than or equal to {minimum}"}
            )
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            issues.append(
                {"path": path, "reason": f"must be less than or equal to {maximum}"}
            )

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value:
                issues.append(
                    {
                        "path": _child_path(path, name),
                        "reason": "missing required argument",
                    }
                )
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            if not isinstance(name, str):
                issues.append(
                    {"path": path, "reason": "object keys must be strings"}
                )
                continue
            child = properties.get(name)
            if child is not None:
                _validate_value(item, child, _child_path(path, name), issues)
            elif additional is False:
                issues.append(
                    {
                        "path": _child_path(path, name),
                        "reason": "unexpected argument",
                    }
                )
            elif isinstance(additional, Mapping):
                _validate_value(
                    item, additional, _child_path(path, name), issues
                )

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]", issues)
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            issues.append(
                {"path": path, "reason": f"must contain at least {min_items} items"}
            )
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            issues.append(
                {"path": path, "reason": f"must contain at most {max_items} items"}
            )


def validate_arguments(
    schema: Mapping[str, Any],
    arguments: Any,
) -> list[dict[str, Any]]:
    """按 JSON Schema 子集校验参数，返回全部问题；空列表表示通过。"""
    if not isinstance(schema, Mapping):
        raise ValueError("schema must be a mapping")
    issues: list[dict[str, Any]] = []
    _validate_value(arguments, schema, "$", issues)
    return issues


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_ERROR_MESSAGE_LENGTH:
        return text
    return text[: _MAX_ERROR_MESSAGE_LENGTH - 1] + "…"


class ToolExecutor:
    """按名称执行工具，并把所有可预期失败转成统一的 ``ToolResult``。"""

    def __init__(self, registry: ToolRegistry) -> None:
        if not isinstance(registry, ToolRegistry):
            raise ValueError("registry must be a ToolRegistry")
        self._registry = registry

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def execute(
        self,
        call: ToolCall,
        *,
        session: Session | None = None,
    ) -> ToolResult:
        """执行一次工具调用；未知工具、参数错误和内部异常都返回失败结果。"""
        if not isinstance(call, ToolCall):
            raise ValueError("call must be a ToolCall")
        if session is not None and not isinstance(session, Session):
            raise ValueError("session must be a Session or None")

        try:
            spec = self._registry.get(call.name)
        except ToolNotFoundError as exc:
            return ToolResult.failure(call.call_id, call.name, exc.to_info())

        issues = validate_arguments(spec.parameters, call.arguments)
        if issues:
            return ToolResult.failure(
                call.call_id,
                call.name,
                ErrorInfo(
                    ErrorCode.INVALID_TOOL_ARGUMENTS,
                    f"工具 {call.name!r} 的参数不符合 Schema",
                    {"tool": call.name, "errors": issues},
                ),
            )

        if spec.requires_session and session is None:
            return ToolResult.failure(
                call.call_id,
                call.name,
                ErrorInfo(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    f"工具 {call.name!r} 需要会话上下文",
                    {"tool": call.name, "reason": "missing_session"},
                ),
            )

        context = ToolContext(session=session)
        try:
            output = spec.handler(dict(call.arguments), context)
        except AgentError as exc:
            return ToolResult.failure(call.call_id, call.name, exc.to_info())
        except Exception as exc:  # noqa: BLE001 - 工具异常必须转为统一结果
            return ToolResult.failure(
                call.call_id,
                call.name,
                ErrorInfo(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    f"工具 {call.name!r} 执行失败",
                    {
                        "tool": call.name,
                        "cause_type": type(exc).__name__,
                        "message": _truncate(str(exc)),
                    },
                ),
            )

        return ToolResult.success(call.call_id, call.name, output)

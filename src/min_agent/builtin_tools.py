"""P2：三个必备工具的实现与默认注册表。

- ``calculator``：手写词法/语法分析，不调用 ``eval``；非法表达式、除零和
  结果过大都转成结构化错误；
- ``search``：本地固定语料 + 确定性兜底，不访问任何网络；
- ``todo``：状态写入 ``Session.tool_state_for("todo")``，天然按会话隔离。

三个工具都只通过 Schema 暴露能力，不在代码中根据用户关键词替模型做选择。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from .errors import ToolArgumentError, ToolExecutionError
from .session import Session
from .tools import ToolContext, ToolRegistry, ToolSpec

# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------

_CALC_MAX_EXPRESSION_LENGTH = 200
_CALC_MAX_NUMBER_DIGITS = 50
_CALC_MAX_EXPONENT = 1000
_CALC_MAX_RESULT_BITS = 100_000


class _CalcError(Exception):
    """计算器内部的稳定失败原因，由处理函数转成 ``ToolExecutionError``。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str


_SINGLE_CHAR_TOKENS = {
    "+": "PLUS",
    "-": "MINUS",
    "*": "MUL",
    "/": "DIV",
    "%": "MOD",
    "(": "LPAREN",
    ")": "RPAREN",
}
_DOUBLE_CHAR_TOKENS = {
    "**": "POW",
    "//": "FLOORDIV",
}


def _tokenize(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    length = len(expression)
    index = 0
    while index < length:
        char = expression[index]
        if char.isspace():
            index += 1
            continue

        if char.isdigit() or (
            char == "."
            and index + 1 < length
            and expression[index + 1].isdigit()
        ):
            start = index
            while index < length and expression[index].isdigit():
                index += 1
            if index < length and expression[index] == ".":
                index += 1
                while index < length and expression[index].isdigit():
                    index += 1
            text = expression[start:index]
            if sum(item.isdigit() for item in text) > _CALC_MAX_NUMBER_DIGITS:
                raise _CalcError("number_too_large", "数字字面量过长")
            tokens.append(_Token("NUMBER", text))
            continue

        pair = expression[index : index + 2]
        if pair in _DOUBLE_CHAR_TOKENS:
            tokens.append(_Token(_DOUBLE_CHAR_TOKENS[pair], pair))
            index += 2
            continue

        kind = _SINGLE_CHAR_TOKENS.get(char)
        if kind is None:
            raise _CalcError("invalid_expression", f"不支持的字符：{char!r}")
        tokens.append(_Token(kind, char))
        index += 1
    return tokens


def _parse_number(text: str) -> int | float:
    if "." in text:
        return float(text)
    return int(text)


def _guard(value: int | float) -> int | float:
    if isinstance(value, int):
        if value.bit_length() > _CALC_MAX_RESULT_BITS:
            raise _CalcError("result_too_large", "计算结果过大")
        return value
    if not math.isfinite(value):
        raise _CalcError("result_too_large", "计算结果超出浮点数范围")
    if value == 0:
        return 0
    return value


def _power(base: int | float, exponent: int | float) -> int | float:
    if not isinstance(exponent, int) or isinstance(exponent, bool):
        raise _CalcError("invalid_expression", "指数必须是整数")
    if abs(exponent) > _CALC_MAX_EXPONENT:
        raise _CalcError(
            "exponent_too_large",
            f"指数绝对值不能超过 {_CALC_MAX_EXPONENT}",
        )
    if base == 0 and exponent < 0:
        raise _CalcError("division_by_zero", "0 不能取负指数")
    if (
        isinstance(base, int)
        and exponent > 0
        and base.bit_length() * exponent > _CALC_MAX_RESULT_BITS
    ):
        raise _CalcError("result_too_large", "计算结果过大")
    try:
        return base**exponent
    except (ArithmeticError, ValueError):
        raise _CalcError("arithmetic_error", "幂运算失败") from None


def _apply_binary(kind: str, left: int | float, right: int | float) -> int | float:
    if kind == "PLUS":
        return _guard(left + right)
    if kind == "MINUS":
        return _guard(left - right)
    if kind == "MUL":
        return _guard(left * right)
    if kind in {"DIV", "FLOORDIV", "MOD"} and right == 0:
        raise _CalcError("division_by_zero", "除数不能为 0")
    if kind == "DIV":
        return _guard(left / right)
    if kind == "FLOORDIV":
        return _guard(left // right)
    if kind == "MOD":
        return _guard(left % right)
    if kind == "POW":
        return _guard(_power(left, right))
    raise _CalcError("invalid_expression", f"未知运算符：{kind!r}")


class _Parser:
    """递归下降解析：加减 < 乘除取模 < 一元正负 < 幂（右结合）。"""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._position = 0

    def parse(self) -> int | float:
        if not self._tokens:
            raise _CalcError("invalid_expression", "表达式为空")
        value = self._expression()
        token = self._peek()
        if token is not None:
            raise _CalcError("invalid_expression", f"意外的标记：{token.text!r}")
        return value

    def _peek(self) -> _Token | None:
        if self._position >= len(self._tokens):
            return None
        return self._tokens[self._position]

    def _take(self, kind: str | None = None) -> _Token:
        token = self._peek()
        if token is None:
            raise _CalcError("invalid_expression", "表达式不完整")
        if kind is not None and token.kind != kind:
            raise _CalcError("invalid_expression", f"意外的标记：{token.text!r}")
        self._position += 1
        return token

    def _expression(self) -> int | float:
        value = self._term()
        while True:
            token = self._peek()
            if token is None or token.kind not in {"PLUS", "MINUS"}:
                return value
            self._position += 1
            right = self._term()
            value = _apply_binary(token.kind, value, right)

    def _term(self) -> int | float:
        value = self._unary()
        while True:
            token = self._peek()
            if token is None or token.kind not in {
                "MUL",
                "DIV",
                "FLOORDIV",
                "MOD",
            }:
                return value
            self._position += 1
            right = self._unary()
            value = _apply_binary(token.kind, value, right)

    def _unary(self) -> int | float:
        token = self._peek()
        if token is not None and token.kind in {"PLUS", "MINUS"}:
            self._position += 1
            value = self._unary()
            return value if token.kind == "PLUS" else _guard(-value)
        return self._power()

    def _power(self) -> int | float:
        value = self._primary()
        token = self._peek()
        if token is not None and token.kind == "POW":
            self._position += 1
            exponent = self._unary()
            value = _apply_binary("POW", value, exponent)
        return value

    def _primary(self) -> int | float:
        token = self._peek()
        if token is None:
            raise _CalcError("invalid_expression", "表达式不完整")
        if token.kind == "NUMBER":
            self._position += 1
            return _parse_number(token.text)
        if token.kind == "LPAREN":
            self._position += 1
            value = self._expression()
            self._take("RPAREN")
            return value
        raise _CalcError("invalid_expression", f"意外的标记：{token.text!r}")


def evaluate_expression(expression: str) -> int | float:
    """计算基础算术表达式；失败时抛出带稳定 ``reason`` 的 ``_CalcError``。"""
    text = expression.strip()
    if not text:
        raise _CalcError("invalid_expression", "表达式为空")
    if len(text) > _CALC_MAX_EXPRESSION_LENGTH:
        raise _CalcError(
            "expression_too_long",
            f"表达式长度不能超过 {_CALC_MAX_EXPRESSION_LENGTH}",
        )
    return _Parser(_tokenize(text)).parse()


def _calculator_handler(
    arguments: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    expression = arguments["expression"].strip()
    if not expression:
        raise ToolArgumentError(
            "expression 不能为空",
            {"argument": "expression"},
        )
    try:
        result = evaluate_expression(expression)
    except _CalcError as exc:
        raise ToolExecutionError(
            exc.message,
            {"reason": exc.reason, "expression": expression},
        ) from None
    return {"expression": expression, "result": result}


# ---------------------------------------------------------------------------
# mock search
# ---------------------------------------------------------------------------

_EMPTY_SEARCH_FALLBACK_COUNT = 5

_SEARCH_FIXTURES: dict[str, tuple[dict[str, str], ...]] = {
    "杭州天气": (
        {
            "title": "杭州天气预报",
            "snippet": "杭州未来三天以多云为主，气温 18-26°C，早晚温差较大。",
            "url": "https://mock.local/weather/hangzhou",
        },
        {
            "title": "杭州出行提示",
            "snippet": "周末西湖景区人流较多，建议错峰出行并携带雨具。",
            "url": "https://mock.local/travel/hangzhou",
        },
    ),
    "python pytest": (
        {
            "title": "pytest 官方文档",
            "snippet": "pytest 是 Python 的测试框架，支持参数化、fixture 与插件。",
            "url": "https://mock.local/docs/pytest",
        },
        {
            "title": "pytest 最佳实践",
            "snippet": "用小型、独立的测试函数，并通过 fixture 复用准备逻辑。",
            "url": "https://mock.local/docs/pytest-best-practices",
        },
    ),
    "周报模板": (
        {
            "title": "团队周报模板",
            "snippet": "周报包含本周完成、下周计划、风险与需要的支持四部分。",
            "url": "https://mock.local/templates/weekly-report",
        },
    ),
}


def _normalize_query(query: str) -> str:
    return " ".join(query.split())


def _search_handler(
    arguments: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    query = _normalize_query(arguments["query"])
    if not query:
        raise ToolArgumentError("query 不能为空", {"argument": "query"})
    limit = arguments.get("limit", 3)
    key = query.casefold()

    fixture = _SEARCH_FIXTURES.get(key)
    if fixture is not None:
        all_results = [dict(item) for item in fixture]
    else:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        all_results = [
            {
                "title": f"「{query}」的模拟搜索结果 {index + 1}",
                "snippet": (
                    f"这是本地 Mock 搜索为「{query}」返回的第 {index + 1} 条固定结果，"
                    "不访问真实搜索服务。"
                ),
                "url": f"https://mock.local/search/{digest}/{index + 1}",
            }
            for index in range(_EMPTY_SEARCH_FALLBACK_COUNT)
        ]

    selected = all_results[:limit]
    return {
        "query": query,
        "results": selected,
        "total": len(all_results),
        "returned": len(selected),
        "source": "mock",
    }


# ---------------------------------------------------------------------------
# todo
# ---------------------------------------------------------------------------

_TODO_MAX_TEXT_LENGTH = 200
_TODO_ACTIONS = ("add", "list", "complete")


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _todo_state(session: Session) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    state = session.tool_state_for("todo")
    items = state.get("items")
    if items is None:
        items = []
        state["items"] = items
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ToolExecutionError(
            "todo 状态已损坏",
            {"reason": "invalid_todo_state"},
        )
    next_id = state.get("next_id", 1)
    if not _is_integer(next_id) or next_id < 1:
        raise ToolExecutionError(
            "todo 状态已损坏",
            {"reason": "invalid_todo_state"},
        )
    state["next_id"] = next_id
    return state, items, next_id


def _find_todo(items: list[dict[str, Any]], todo_id: int) -> dict[str, Any] | None:
    for item in items:
        if item.get("id") == todo_id:
            return item
    return None


def _todo_handler(
    arguments: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    session = context.require_session("todo")
    state, items, next_id = _todo_state(session)
    action = arguments["action"]

    if action == "add":
        text = arguments.get("text")
        if text is None:
            raise ToolArgumentError("add 操作需要 text", {"argument": "text"})
        text = text.strip()
        if not text:
            raise ToolArgumentError("text 不能为空", {"argument": "text"})
        if len(text) > _TODO_MAX_TEXT_LENGTH:
            raise ToolArgumentError(
                f"text 不能超过 {_TODO_MAX_TEXT_LENGTH} 个字符",
                {"argument": "text"},
            )
        item = {"id": next_id, "text": text, "done": False}
        items.append(item)
        state["next_id"] = next_id + 1
        return {"action": "add", "item": dict(item), "total": len(items)}

    if action == "list":
        return {
            "action": "list",
            "items": [dict(item) for item in items],
            "total": len(items),
        }

    if action == "complete":
        todo_id = arguments.get("id")
        if todo_id is None:
            raise ToolArgumentError("complete 操作需要 id", {"argument": "id"})
        item = _find_todo(items, todo_id)
        if item is None:
            raise ToolExecutionError(
                f"待办 {todo_id} 不存在",
                {"reason": "todo_not_found", "todo_id": todo_id},
            )
        already_completed = bool(item.get("done"))
        item["done"] = True
        return {
            "action": "complete",
            "item": dict(item),
            "already_completed": already_completed,
            "total": len(items),
        }

    raise ToolArgumentError(
        f"未知的 action：{action!r}",
        {"argument": "action", "allowed": list(_TODO_ACTIONS)},
    )


# ---------------------------------------------------------------------------
# specs and default registry
# ---------------------------------------------------------------------------


def calculator_tool() -> ToolSpec:
    return ToolSpec(
        name="calculator",
        description=(
            "计算基础算术表达式，支持 +、-、*、/、//、%、** 和括号。"
            "需要精确数值计算时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _CALC_MAX_EXPRESSION_LENGTH,
                    "description": "要计算的算术表达式，例如 (1+2)*3",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        handler=_calculator_handler,
    )


def search_tool() -> ToolSpec:
    return ToolSpec(
        name="search",
        description=(
            "在本地模拟资料库中搜索并返回稳定结果，用于查询天气、文档等公开信息。"
            "不访问真实搜索服务。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "搜索关键词",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3,
                    "description": "最多返回的结果条数，默认 3",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_search_handler,
    )


def todo_tool() -> ToolSpec:
    return ToolSpec(
        name="todo",
        description=(
            "管理当前会话的待办事项。add 新增待办，list 列出全部待办，"
            "complete 按 id 标记完成。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_TODO_ACTIONS),
                    "description": "操作类型",
                },
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _TODO_MAX_TEXT_LENGTH,
                    "description": "add 操作时的待办内容",
                },
                "id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "complete 操作时的待办 id",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=_todo_handler,
        requires_session=True,
    )


def create_default_registry() -> ToolRegistry:
    """注册三个必备工具；顺序固定为 calculator、search、todo。"""
    registry = ToolRegistry()
    for spec in (calculator_tool(), search_tool(), todo_tool()):
        registry.register(spec)
    return registry

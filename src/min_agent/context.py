"""P1/P4：把 Session 的历史组装成有界、可直接发给模型的 Context。

Context 的固定顺序：

1. 系统指令（角色约束）；
2. 滚动摘要（仅当存在较早对话被压缩时）；
3. 近期完整轮次（用户输入、工具调用、工具结果、最终答案）；
4. 当前用户输入。

``build_turn_context`` 供 P4 Loop 使用：当前轮次尚未写入 Session 时，在历史之后
追加本轮用户输入和已经发生的工具调用/结果，避免重复追加用户输入。

约定：

- 工具调用以 assistant 消息的 ``tool_calls`` 字段保留，工具结果紧随其后，
  通过 ``tool_call_id`` 对应，二者不会被拆散；
- ``thought_summary`` 只用于解析与日志，不写回 Context；
- 工具 Schema 属于当前模型请求的能力信息，由 P3 在请求层传入，
  不重复写入每一轮历史。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .models import Message, Role, ToolExchange, ToolResult, Turn
from .session import (
    DEFAULT_SYSTEM_PROMPT,
    SUMMARY_MESSAGE_NAME,
    SUMMARY_PREFIX,
    Session,
)


def build_context(
    session: Session,
    user_input: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> list[Message]:
    """组装一次模型请求所需的完整消息列表。"""
    if not isinstance(session, Session):
        raise ValueError("session must be a Session")
    _validate_text(user_input, "user_input")
    _validate_text(system_prompt, "system_prompt")

    messages = _history_messages(session, system_prompt)
    messages.append(Message(role=Role.USER, content=user_input))
    return messages


def build_turn_context(
    session: Session,
    turn: Turn,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    notices: Sequence[str] = (),
) -> list[Message]:
    """组装进行中轮次的模型上下文。

    P4 的 Loop 在一次轮次内可能多次调用模型：用户输入 → 工具调用/结果 →
    再次判断。此时当前轮次尚未写入 Session，因此这里在历史之后追加
    ``turn.user_input`` 和已经发生的工具调用/结果，而不是再追加一条用户输入。

    ``notices`` 用于把“上一次响应无法解析”等纠错提示追加为最后一条用户消息，
    只在紧接的重试请求中出现，不写入 Session 历史。
    """
    if not isinstance(session, Session):
        raise ValueError("session must be a Session")
    if not isinstance(turn, Turn):
        raise ValueError("turn must be a Turn")
    if turn.session_id != session.session_id:
        raise ValueError(
            f"turn belongs to session {turn.session_id!r}, "
            f"not {session.session_id!r}"
        )
    if not turn.is_open:
        raise ValueError("only open turns can be converted to a request context")
    _validate_text(system_prompt, "system_prompt")
    if isinstance(notices, (str, bytes)) or not isinstance(notices, Sequence):
        raise ValueError("notices must be a sequence of strings")

    messages = _history_messages(session, system_prompt)
    messages.append(Message(role=Role.USER, content=turn.user_input))
    for exchange in turn.tool_exchanges:
        messages.extend(exchange_to_messages(exchange))
    for notice in notices:
        _validate_text(notice, "notice")
        messages.append(Message(role=Role.USER, content=notice))
    return messages


def _history_messages(session: Session, system_prompt: str) -> list[Message]:
    messages = [Message(role=Role.SYSTEM, content=system_prompt)]
    if session.summary:
        messages.append(
            Message(
                role=Role.SYSTEM,
                content=f"{SUMMARY_PREFIX}\n{session.summary}",
                name=SUMMARY_MESSAGE_NAME,
            )
        )
    for turn in session.turns:
        messages.extend(turn_to_messages(turn))
    return messages


def turn_to_messages(turn: Turn) -> list[Message]:
    """把一个完整轮次展开为消息：用户输入 → 工具调用/结果对 → 最终答案。"""
    if not isinstance(turn, Turn):
        raise ValueError("turn must be a Turn")
    if not turn.is_completed:
        raise ValueError("only completed turns can be converted to messages")

    messages = [Message(role=Role.USER, content=turn.user_input)]
    for exchange in turn.tool_exchanges:
        messages.extend(exchange_to_messages(exchange))
    if turn.answer is None:
        raise ValueError("completed turn requires an answer")
    messages.append(Message(role=Role.ASSISTANT, content=turn.answer))
    return messages


def exchange_to_messages(exchange: ToolExchange) -> list[Message]:
    """把一次工具调用及结果展开为 assistant(tool_calls) + tool 两条消息。"""
    if not isinstance(exchange, ToolExchange):
        raise ValueError("exchange must be a ToolExchange")
    return [
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=(exchange.tool_call,),
        ),
        Message(
            role=Role.TOOL,
            content=tool_result_content(exchange.tool_result),
            name=exchange.tool_result.name,
            tool_call_id=exchange.tool_result.call_id,
        ),
    ]


def tool_result_content(result: ToolResult) -> str:
    """工具结果的稳定 JSON 文本，供模型读取与测试断言。"""
    if not isinstance(result, ToolResult):
        raise ValueError("result must be a ToolResult")
    payload: dict[str, Any] = {"ok": result.ok}
    if result.ok:
        payload["output"] = result.output
    else:
        payload["error"] = result.error.to_dict() if result.error else None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _validate_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value

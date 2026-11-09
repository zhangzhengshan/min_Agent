"""P1：把 Session 的历史组装成有界、可直接发给模型的 Context。

Context 的固定顺序：

1. 系统指令（角色约束）；
2. 滚动摘要（仅当存在较早对话被压缩时）；
3. 近期完整轮次（用户输入、工具调用、工具结果、最终答案）；
4. 当前用户输入。

约定：

- 工具调用以 assistant 消息的 ``tool_calls`` 字段保留，工具结果紧随其后，
  通过 ``tool_call_id`` 对应，二者不会被拆散；
- ``thought_summary`` 只用于解析与日志，不写回 Context；
- 工具 Schema 属于当前模型请求的能力信息，由 P3 在请求层传入，
  不重复写入每一轮历史。
"""

from __future__ import annotations

import json
from typing import Any

from .models import Message, Role, ToolResult, Turn
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
    messages.append(Message(role=Role.USER, content=user_input))
    return messages


def turn_to_messages(turn: Turn) -> list[Message]:
    """把一个完整轮次展开为消息：用户输入 → 工具调用/结果对 → 最终答案。"""
    if not isinstance(turn, Turn):
        raise ValueError("turn must be a Turn")
    if not turn.is_completed:
        raise ValueError("only completed turns can be converted to messages")

    messages = [Message(role=Role.USER, content=turn.user_input)]
    for exchange in turn.tool_exchanges:
        messages.append(
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(exchange.tool_call,),
            )
        )
        messages.append(
            Message(
                role=Role.TOOL,
                content=tool_result_content(exchange.tool_result),
                name=exchange.tool_result.name,
                tool_call_id=exchange.tool_result.call_id,
            )
        )
    if turn.answer is None:
        raise ValueError("completed turn requires an answer")
    messages.append(Message(role=Role.ASSISTANT, content=turn.answer))
    return messages


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

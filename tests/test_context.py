"""P1 Context 组装测试：顺序、隔离、工具调用保留与压缩后的有界性。"""

from __future__ import annotations

import json

import pytest

from min_agent.context import (
    build_context,
    tool_result_content,
    turn_to_messages,
)
from min_agent.errors import ErrorCode, ErrorInfo
from min_agent.models import Role, ToolCall, ToolResult, Turn
from min_agent.session import (
    SUMMARY_MESSAGE_NAME,
    SUMMARY_PREFIX,
    Session,
)


class FixedSummarizer:
    def summarize(self, previous_summary, turns):
        return "较早对话摘要：" + "、".join(turn.user_input for turn in turns)


def make_turn(
    session_id: str = "window-1",
    user_input: str = "hi",
    answer: str = "hello",
    exchanges: list[tuple[ToolCall, ToolResult]] | None = None,
    thought_summary: str | None = None,
) -> Turn:
    turn = Turn(session_id=session_id, user_input=user_input)
    for call, result in exchanges or []:
        turn.add_exchange(call, result, thought_summary=thought_summary)
    turn.complete(answer)
    return turn


def test_context_order_is_system_summary_recent_turns_then_input() -> None:
    session = Session(
        "window-1", summarizer=FixedSummarizer(), max_turns=2, recent_turns=1
    )
    for index in range(1, 4):
        session.record_turn(
            make_turn(user_input=f"q{index}", answer=f"a{index}")
        )

    messages = build_context(session, "继续", system_prompt="系统指令")

    assert [message.role for message in messages] == [
        Role.SYSTEM,
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
    ]
    assert messages[0].content == "系统指令"
    assert messages[1].name == SUMMARY_MESSAGE_NAME
    assert messages[1].content == f"{SUMMARY_PREFIX}\n{session.summary}"
    assert messages[2].content == "q3"
    assert messages[3].content == "a3"
    assert messages[4].content == "继续"


def test_context_without_summary_only_has_system_prompt_and_input() -> None:
    session = Session("window-1")
    messages = build_context(session, "你好")

    assert [message.role for message in messages] == [Role.SYSTEM, Role.USER]
    assert messages[1].content == "你好"


def test_context_does_not_leak_other_session_history() -> None:
    session_1 = Session("window-1")
    session_2 = Session("window-2")
    session_1.record_turn(make_turn("window-1", "我住在杭州", "记住了"))
    session_2.record_turn(make_turn("window-2", "我在写周报", "好的"))

    context_1 = build_context(session_1, "我住哪")
    context_2 = build_context(session_2, "我在做什么")

    text_1 = "\n".join(message.content for message in context_1)
    text_2 = "\n".join(message.content for message in context_2)

    assert "我住在杭州" in text_1
    assert "我在写周报" not in text_1
    assert "我在写周报" in text_2
    assert "我住在杭州" not in text_2


def test_context_keeps_tool_call_and_result_as_adjacent_messages() -> None:
    call = ToolCall(
        call_id="call-1", name="calculator", arguments={"expression": "1+1"}
    )
    result = ToolResult.success("call-1", "calculator", output=2)
    session = Session("window-1")
    session.record_turn(
        make_turn(
            user_input="1+1 等于几",
            answer="等于 2",
            exchanges=[(call, result)],
            thought_summary="需要计算",
        )
    )

    messages = build_context(session, "再算一次")

    tool_call_index = next(
        index for index, message in enumerate(messages) if message.tool_calls
    )
    assistant_message = messages[tool_call_index]
    tool_message = messages[tool_call_index + 1]

    assert assistant_message.role is Role.ASSISTANT
    assert assistant_message.content == ""
    assert assistant_message.tool_calls == (call,)
    assert tool_message.role is Role.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.name == "calculator"
    assert json.loads(tool_message.content) == {"ok": True, "output": 2}
    assert messages[-2].content == "等于 2"


def test_failed_tool_result_is_represented_as_structured_error() -> None:
    call = ToolCall(
        call_id="call-2", name="calculator", arguments={"expression": "1/0"}
    )
    error = ErrorInfo(ErrorCode.TOOL_EXECUTION_FAILED, "division by zero")
    result = ToolResult.failure("call-2", "calculator", error)
    session = Session("window-1")
    session.record_turn(
        make_turn(
            user_input="1/0",
            answer="不能除以零",
            exchanges=[(call, result)],
        )
    )

    messages = build_context(session, "为什么")
    tool_message = next(
        message for message in messages if message.role is Role.TOOL
    )

    assert tool_message.tool_call_id == "call-2"
    assert tool_message.name == "calculator"
    assert json.loads(tool_message.content) == {
        "ok": False,
        "error": error.to_dict(),
    }


def test_thought_summary_is_not_written_back_to_context() -> None:
    thought = "这是内部判断，不应回灌到 Context"
    call = ToolCall(call_id="call-1", name="search", arguments={"query": "pytest"})
    result = ToolResult.success("call-1", "search", output=["pytest docs"])
    session = Session("window-1")
    session.record_turn(
        make_turn(
            user_input="查一下 pytest",
            answer="找到官方文档",
            exchanges=[(call, result)],
            thought_summary=thought,
        )
    )

    messages = build_context(session, "继续")

    assert all(thought not in message.content for message in messages)
    assert all(
        thought not in json.dumps(call.arguments, ensure_ascii=False)
        for message in messages
        for call in message.tool_calls
    )


def test_compaction_bounds_context_and_keeps_recent_tool_pair_intact() -> None:
    call = ToolCall(
        call_id="call-3", name="calculator", arguments={"expression": "2+2"}
    )
    result = ToolResult.success("call-3", "calculator", output=4)
    session = Session(
        "window-1", summarizer=FixedSummarizer(), max_turns=3, recent_turns=2
    )
    session.record_turn(make_turn(user_input="q1", answer="a1"))
    session.record_turn(make_turn(user_input="q2", answer="a2"))
    session.record_turn(
        make_turn(
            user_input="q3",
            answer="a3",
            exchanges=[(call, result)],
            thought_summary="需要计算",
        )
    )
    session.record_turn(make_turn(user_input="q4", answer="a4"))

    assert session.summary is not None
    assert [turn.user_input for turn in session.turns] == ["q3", "q4"]

    messages = build_context(session, "q5")
    user_messages = [
        message.content for message in messages if message.role is Role.USER
    ]
    assert user_messages == ["q3", "q4", "q5"]

    tool_call_index = next(
        index for index, message in enumerate(messages) if message.tool_calls
    )
    assert messages[tool_call_index].tool_calls == (call,)
    assert messages[tool_call_index + 1].role is Role.TOOL
    assert messages[tool_call_index + 1].tool_call_id == "call-3"


def test_conversation_follow_up_uses_own_history_not_other_session() -> None:
    session_1 = Session("window-1")
    session_2 = Session("window-2")
    session_1.record_turn(make_turn("window-1", "项目代号是蓝色", "好的"))
    session_2.record_turn(make_turn("window-2", "项目代号是绿色", "好的"))

    answer_context = build_context(session_1, "项目代号是什么")

    assistant_history = [
        message.content
        for message in answer_context
        if message.role is Role.ASSISTANT
    ]
    assert assistant_history == ["好的"]
    user_history = [
        message.content
        for message in answer_context
        if message.role is Role.USER
    ]
    assert user_history == ["项目代号是蓝色", "项目代号是什么"]


@pytest.mark.parametrize(
    "user_input",
    ["", "   ", None, 123],
)
def test_build_context_rejects_invalid_user_input(user_input) -> None:
    with pytest.raises(ValueError):
        build_context(Session("window-1"), user_input)


def test_build_context_rejects_invalid_session_and_system_prompt() -> None:
    with pytest.raises(ValueError):
        build_context(object(), "hi")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_context(Session("window-1"), "hi", system_prompt="   ")


def test_turn_to_messages_rejects_open_turn() -> None:
    with pytest.raises(ValueError, match="completed"):
        turn_to_messages(Turn(session_id="window-1", user_input="hi"))


def test_tool_result_content_is_stable_and_readable() -> None:
    result = ToolResult.success("call-1", "search", output={"items": ["a"]})

    content = tool_result_content(result)

    assert content == '{"ok": true, "output": {"items": ["a"]}}'
    assert json.loads(content)["output"] == {"items": ["a"]}

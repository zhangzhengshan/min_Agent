"""P5 端到端集成测试：双 Session、两类追问、压缩追问与工具 trace。

全部使用 ``MockLLMClient`` 与 Mock ``search``，不访问网络、不读取真实 API Key；
``tests/conftest.py`` 会额外拦截网络并清空 LLM 环境变量。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from min_agent.builtin_tools import create_default_registry
from min_agent.cli import AgentCLI
from min_agent.context import tool_result_content
from min_agent.llm import MockLLMClient
from min_agent.loop import AgentLoop
from min_agent.models import Role, Turn
from min_agent.session import (
    SUMMARY_MESSAGE_NAME,
    SUMMARY_PREFIX,
    SessionManager,
)
from min_agent.trace import MemoryTraceSink

REGISTRY = create_default_registry()


def final(answer: str, *, thought: str | None = None) -> str:
    payload: dict[str, object] = {"kind": "final", "answer": answer}
    if thought is not None:
        payload["thought_summary"] = thought
    return json.dumps(payload, ensure_ascii=False)


def tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str | None = None,
    thought: str | None = None,
) -> str:
    call: dict[str, object] = {"name": name, "arguments": arguments}
    if call_id is not None:
        call["call_id"] = call_id
    payload: dict[str, object] = {"kind": "tool_call", "tool_call": call}
    if thought is not None:
        payload["thought_summary"] = thought
    return json.dumps(payload, ensure_ascii=False)


def make_cli(
    responses: Sequence[str | BaseException],
    *,
    sessions: SessionManager | None = None,
    loop_kwargs: dict[str, object] | None = None,
) -> tuple[AgentCLI, list[str], SessionManager, MockLLMClient, MemoryTraceSink]:
    client = MockLLMClient(responses)
    manager = sessions if sessions is not None else SessionManager()
    sink = MemoryTraceSink()
    loop = AgentLoop(
        client,
        create_default_registry(),
        trace_sink=sink,
        **(loop_kwargs or {}),  # type: ignore[arg-type]
    )
    output: list[str] = []
    cli = AgentCLI(loop, manager, output=output.append)
    return cli, output, manager, client, sink


def _request_text(client: MockLLMClient, index: int) -> str:
    return "\n".join(message.content for message in client.requests[index].messages)


def _tool_messages(client: MockLLMClient, index: int) -> list:
    return [m for m in client.requests[index].messages if m.role is Role.TOOL]


def _todo_texts(session) -> list[str]:
    state = session.tool_state_for("todo")
    return [item["text"] for item in state.get("items", [])]


# ---------------------------------------------------------------------------
# 双 Session：天气 + 待办 / 周报 + 待办
# ---------------------------------------------------------------------------


def test_dual_session_weather_and_weekly_report_scenario() -> None:
    """window-1 查天气记待办，window-2 写周报记待办，切回后各自续聊。"""
    cli, output, sessions, client, sink = make_cli(
        [
            # window-1：查杭州天气 → 记待办 → 总结
            tool_call("search", {"query": "杭州天气"}, call_id="w1-s1"),
            tool_call("todo", {"action": "add", "text": "带伞"}, call_id="w1-t1"),
            final("杭州未来三天多云，18-26°C；已记录待办：带伞。"),
            # window-2：查周报模板 → 记待办 → 写要点
            tool_call("search", {"query": "周报模板"}, call_id="w2-s1"),
            tool_call("todo", {"action": "add", "text": "写周报"}, call_id="w2-t1"),
            final("周报要点：本周完成、下周计划、风险与需要的支持；已记录待办：写周报。"),
            # window-1 切回：列待办 → 回答
            tool_call("todo", {"action": "list"}, call_id="w1-l1"),
            final("你的待办是带伞；杭州未来三天多云，18-26°C。"),
            # window-2 切回：列待办 → 回答
            tool_call("todo", {"action": "list"}, call_id="w2-l1"),
            final("你的待办是写周报；周报模板包含四个部分。"),
        ]
    )

    assert cli.handle_line("帮我查一下杭州天气，再记一条待办：带伞") is True
    assert output[-1].startswith("助手：")

    assert cli.handle_line("/new window-2") is True
    cli.handle_line("查一下周报模板，写一份周报要点，再记待办：写周报")

    assert cli.handle_line("/use window-1") is True
    cli.handle_line("我的待办有哪些？杭州天气怎么样？")
    assert output[-1] == "助手：你的待办是带伞；杭州未来三天多云，18-26°C。"

    assert cli.handle_line("/use window-2") is True
    cli.handle_line("我的待办有哪些？周报要点是什么？")
    assert output[-1] == "助手：你的待办是写周报；周报模板包含四个部分。"

    window_1 = sessions.get("window-1")
    window_2 = sessions.get("window-2")
    assert window_1.turn_count == 2
    assert window_2.turn_count == 2
    assert _todo_texts(window_1) == ["带伞"]
    assert _todo_texts(window_2) == ["写周报"]

    # window-1 的追问上下文包含自己的前文和先前工具结果，不串入 window-2。
    window_1_text = _request_text(client, 6)
    assert "杭州天气" in window_1_text
    assert "杭州未来三天以多云为主" in window_1_text  # 第一轮 search 结果
    assert "带伞" in window_1_text
    assert "周报" not in window_1_text
    assert "写周报" not in window_1_text

    # window-2 同理，只看到周报相关内容。
    window_2_text = _request_text(client, 8)
    assert "周报模板" in window_2_text
    assert "团队周报模板" in window_2_text  # 第一轮 search 结果
    assert "写周报" in window_2_text
    assert "带伞" not in window_2_text
    assert "杭州" not in window_2_text

    # 两个 Session 的追问都重新调用了 todo，并拿到各自的数据。
    assert "带伞" in _tool_messages(client, 7)[-1].content
    assert "写周报" in _tool_messages(client, 9)[-1].content
    assert "带伞" not in _tool_messages(client, 9)[-1].content

    # 每次请求都携带三个工具的完整 Schema。
    for request in client.requests:
        assert request.tool_names == ("calculator", "search", "todo")

    # trace 覆盖两个 Session 的全部工具调用，可用 session_id + turn_id 串联。
    assert [
        (trace.session_id, trace.tool, trace.status) for trace in sink.traces
    ] == [
        ("window-1", "search", "success"),
        ("window-1", "todo", "success"),
        ("window-2", "search", "success"),
        ("window-2", "todo", "success"),
        ("window-1", "todo", "success"),
        ("window-2", "todo", "success"),
    ]

    window_1_turn_1 = window_1.turns[0]
    window_1_turn_2 = window_1.turns[1]
    first_turn_traces = [
        trace for trace in sink.traces if trace.turn_id == window_1_turn_1.turn_id
    ]
    follow_up_traces = [
        trace for trace in sink.traces if trace.turn_id == window_1_turn_2.turn_id
    ]
    assert [trace.sequence for trace in first_turn_traces] == [1, 2]
    assert [trace.arguments for trace in first_turn_traces] == [
        {"query": "杭州天气"},
        {"action": "add", "text": "带伞"},
    ]
    assert [trace.sequence for trace in follow_up_traces] == [1]
    assert follow_up_traces[0].arguments == {"action": "list"}


# ---------------------------------------------------------------------------
# 纯对话追问
# ---------------------------------------------------------------------------


def test_pure_conversation_followup_uses_only_its_own_session_history() -> None:
    cli, output, sessions, client, _ = make_cli(
        [
            final("记住了：你叫小林，正在准备毕业设计。"),
            final("抱歉，这是新的会话，我不知道你的名字。"),
            final("你叫小林，正在准备毕业设计。"),
        ]
    )

    cli.handle_line("我叫小林，正在准备毕业设计。")
    assert output[-1] == "助手：记住了：你叫小林，正在准备毕业设计。"

    cli.handle_line("/new window-2")
    cli.handle_line("我叫什么？")
    assert output[-1] == "助手：抱歉，这是新的会话，我不知道你的名字。"

    cli.handle_line("/use window-1")
    cli.handle_line("我叫什么？在做什么？")
    assert output[-1] == "助手：你叫小林，正在准备毕业设计。"

    window_2_text = _request_text(client, 1)
    assert "小林" not in window_2_text
    assert "毕业设计" not in window_2_text

    window_1_text = _request_text(client, 2)
    assert "我叫小林，正在准备毕业设计。" in window_1_text
    assert "记住了：你叫小林，正在准备毕业设计。" in window_1_text
    assert "新的会话" not in window_1_text

    assert sessions.get("window-1").turn_count == 2
    assert sessions.get("window-2").turn_count == 1


# ---------------------------------------------------------------------------
# 基于工具结果的追问，并继续调用工具
# ---------------------------------------------------------------------------


def test_tool_result_followup_reuses_previous_result_and_calls_another_tool() -> None:
    client = MockLLMClient(
        [
            tool_call("search", {"query": "杭州天气"}, call_id="s1"),
            final("杭州未来三天多云，气温 18-26°C。"),
            tool_call("todo", {"action": "add", "text": "带伞"}, call_id="t1"),
            final("多云天气建议带伞，已加入待办。"),
        ]
    )
    loop = AgentLoop(client, REGISTRY)
    session = SessionManager().create("window-1")

    first = loop.run(session, "帮我查一下杭州天气")
    second = loop.run(session, "那需要带伞吗？顺便把带伞加入待办。")

    assert first.ok and second.ok
    assert first.turn.turn_id != second.turn.turn_id
    assert session.turn_count == 2
    assert _todo_texts(session) == ["带伞"]

    # 追问的模型输入同时包含上一轮的工具调用、工具结果和最终答案。
    followup_text = _request_text(client, 2)
    assert "杭州未来三天以多云为主" in followup_text
    assert "杭州未来三天多云，气温 18-26°C。" in followup_text
    prior_tool_messages = [
        message for message in _tool_messages(client, 2)
        if "杭州未来三天以多云为主" in message.content
    ]
    assert len(prior_tool_messages) == 1
    assert prior_tool_messages[0].tool_call_id == "s1"

    prior_calls = [m for m in client.requests[2].messages if m.tool_calls]
    assert [call.tool_calls[0].name for call in prior_calls] == ["search"]

    # 本轮新增的工具结果也会反馈给模型后再生成最终答案。
    assert "带伞" in _tool_messages(client, 3)[-1].content


# ---------------------------------------------------------------------------
# 超最大轮次后的压缩追问
# ---------------------------------------------------------------------------


class RecordingSummarizer:
    """把旧轮次压成包含用户输入、工具结果和答案的确定性摘要。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, tuple[str, ...]]] = []

    def summarize(
        self,
        previous_summary: str | None,
        turns: Sequence[Turn],
    ) -> str:
        self.calls.append(
            (previous_summary, tuple(turn.user_input for turn in turns))
        )
        parts: list[str] = []
        if previous_summary:
            parts.append(previous_summary)
        for turn in turns:
            parts.append(f"用户：{turn.user_input}")
            for exchange in turn.tool_exchanges:
                parts.append(
                    f"工具 {exchange.tool_call.name} 返回："
                    f"{tool_result_content(exchange.tool_result)}"
                )
            parts.append(f"助手：{turn.answer}")
        return "；".join(parts)


def test_followup_after_compression_uses_summary_recent_turns_and_todo_state() -> None:
    summarizer = RecordingSummarizer()
    sessions = SessionManager(
        summarizer=summarizer,
        max_turns=4,
        recent_turns=2,
    )
    cli, output, _, client, _ = make_cli(
        [
            # 轮次 1：天气 + 工具结果
            tool_call("search", {"query": "杭州天气"}, call_id="c1"),
            final("杭州未来三天多云，气温 18-26°C。"),
            # 轮次 2：待办
            tool_call("todo", {"action": "add", "text": "带伞"}, call_id="c2"),
            final("已加入待办：带伞。"),
            # 轮次 3：周报模板
            tool_call("search", {"query": "周报模板"}, call_id="c3"),
            final("周报模板包含本周完成、下周计划、风险和支持。"),
            # 轮次 4：列待办
            tool_call("todo", {"action": "list"}, call_id="c4"),
            final("你的待办有 1 条。"),
            # 轮次 5：关键事实
            final("记住了：项目代号是蓝鲸。"),
            # 轮次 6：压缩后的追问
            tool_call("todo", {"action": "list"}, call_id="c5"),
            final("项目代号是蓝鲸；杭州多云；待办有带伞。"),
        ],
        sessions=sessions,
    )

    session = sessions.get("window-1")
    cli.handle_line("帮我查一下杭州天气")
    cli.handle_line("把带伞加入待办")
    cli.handle_line("周报模板是什么？")
    cli.handle_line("我的待办有几条？")
    cli.handle_line("记住：项目代号是蓝鲸。")

    # 第 5 轮触发压缩：前 3 轮进入摘要，只保留最近 2 个完整轮次。
    assert session.summary is not None
    assert session.turn_count == 2
    assert [turn.user_input for turn in session.turns] == [
        "我的待办有几条？",
        "记住：项目代号是蓝鲸。",
    ]
    assert summarizer.calls == [
        (
            None,
            ("帮我查一下杭州天气", "把带伞加入待办", "周报模板是什么？"),
        )
    ]
    summary = session.summary
    assert "杭州未来三天以多云为主" in summary
    assert "带伞" in summary
    assert "周报模板" in summary

    cli.handle_line("刚才让你记住的项目代号是什么？杭州天气如何？我的待办有哪些？")
    assert output[-1] == "助手：项目代号是蓝鲸；杭州多云；待办有带伞。"

    # 追问请求：system + 滚动摘要 + 最近两轮 + 当前输入。
    request = client.requests[9]
    assert request.messages[0].role is Role.SYSTEM
    assert request.messages[1].role is Role.SYSTEM
    assert request.messages[1].name == SUMMARY_MESSAGE_NAME
    assert request.messages[1].content.startswith(SUMMARY_PREFIX)
    assert "带伞" in request.messages[1].content
    assert "周报模板" in request.messages[1].content

    history_bodies = [message.content for message in request.messages[2:]]
    assert any("蓝鲸" in body for body in history_bodies)  # 最近轮次完整保留
    assert any("你的待办有 1 条。" in body for body in history_bodies)
    assert not any("帮我查一下杭州天气" in body for body in history_bodies)
    assert not any("周报模板是什么？" in body for body in history_bodies)
    assert sum(
        1 for message in request.messages[2:] if message.role is Role.USER
    ) == 3  # 最近两轮 + 当前输入

    # 压缩不丢工具状态：上一个被压缩的 todo add 仍可列出。
    assert _todo_texts(session) == ["带伞"]
    assert "带伞" in _tool_messages(client, 10)[-1].content
    assert len(client.requests) == 11

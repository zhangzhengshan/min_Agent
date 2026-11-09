"""P4 核心 Loop 测试：直接回复、工具续判、上限、重试与异常路径。"""

from __future__ import annotations

import json

import pytest

from min_agent.builtin_tools import create_default_registry
from min_agent.errors import ErrorCode, ModelError, ParseError
from min_agent.llm import MockLLMClient
from min_agent.loop import AgentLoop
from min_agent.models import Role
from min_agent.session import Session
from min_agent.tools import ToolExecutor, ToolRegistry
from min_agent.trace import MemoryTraceSink

REGISTRY = create_default_registry()


def final(answer: str, thought: str | None = None) -> str:
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


class ExplodingSummarizer:
    def summarize(self, previous_summary, turns):  # noqa: ANN001 - 测试替身
        raise RuntimeError("summarizer boom")


class BrokenTraceSink:
    def record(self, trace) -> None:  # noqa: ANN001 - 测试替身
        raise OSError("disk full")


# ---------------------------------------------------------------------------
# direct answer and tool continuation
# ---------------------------------------------------------------------------


def test_final_response_skips_tools_and_records_turn() -> None:
    client = MockLLMClient([final("你好")])
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink)
    session = Session("window-1")

    result = loop.run(session, "你好")

    assert result.ok
    assert result.recorded
    assert result.answer == "你好"
    assert result.tool_calls == 0
    assert result.malformed_retries == 0
    assert session.turn_count == 1
    recorded = session.turns[0]
    assert recorded.is_completed
    assert recorded.answer == "你好"
    assert recorded.tool_exchanges == []
    assert sink.traces == ()
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.tool_names == ("calculator", "search", "todo")
    assert request.messages[-1].role is Role.USER
    assert request.messages[-1].content == "你好"


def test_tool_result_is_fed_back_before_final_answer() -> None:
    client = MockLLMClient(
        [
            tool_call(
                "calculator",
                {"expression": "123*45"},
                call_id="call-calc",
                thought="需要精确计算",
            ),
            final("123*45 的结果是 5535"),
        ]
    )
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink)
    session = Session("window-1")

    result = loop.run(session, "帮我算 123*45")

    assert result.ok
    assert result.tool_calls == 1
    assert session.turn_count == 1
    exchange = result.turn.tool_exchanges[0]
    assert exchange.tool_result.ok
    assert exchange.tool_result.output["result"] == 5535
    assert exchange.thought_summary == "需要精确计算"

    second = client.requests[1]
    tool_messages = [m for m in second.messages if m.role is Role.TOOL]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-calc"
    assert '"result": 5535' in tool_messages[0].content
    assistant_calls = [m for m in second.messages if m.tool_calls]
    assert len(assistant_calls) == 1
    assert assistant_calls[0].tool_calls[0].call_id == "call-calc"

    trace = sink.traces[0]
    assert trace.session_id == "window-1"
    assert trace.turn_id == result.turn.turn_id
    assert trace.sequence == 1
    assert trace.tool == "calculator"
    assert trace.arguments == {"expression": "123*45"}
    assert trace.status == "success"
    assert trace.duration_ms >= 0
    assert trace.error is None


def test_consecutive_tool_calls_continue_until_final() -> None:
    client = MockLLMClient(
        [
            tool_call("calculator", {"expression": "1+1"}),
            tool_call("search", {"query": "杭州天气"}, thought="再查天气"),
            final("算完了，也查到天气了"),
        ]
    )
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink)
    session = Session("window-1")

    result = loop.run(session, "先算 1+1，再查杭州天气")

    assert result.ok
    assert result.tool_calls == 2
    assert [trace.sequence for trace in sink.traces] == [1, 2]
    assert [trace.tool for trace in sink.traces] == ["calculator", "search"]
    assert len([m for m in client.requests[1].messages if m.role is Role.TOOL]) == 1
    assert len([m for m in client.requests[2].messages if m.role is Role.TOOL]) == 2
    assert session.turn_count == 1
    assert len(session.turns[0].tool_exchanges) == 2


# ---------------------------------------------------------------------------
# malformed output
# ---------------------------------------------------------------------------


def test_malformed_response_gets_one_retry_with_notice() -> None:
    client = MockLLMClient(["这不是 JSON", final("好的")])
    loop = AgentLoop(client, REGISTRY)
    session = Session("window-1")

    result = loop.run(session, "你好")

    assert result.ok
    assert result.malformed_retries == 1
    assert len(client.requests) == 2
    notice = client.requests[1].messages[-1]
    assert notice.role is Role.USER
    assert "无法解析" in notice.content
    assert session.turn_count == 1


def test_malformed_twice_stops_turn_with_clear_error() -> None:
    client = MockLLMClient(["bad", "still bad"])
    loop = AgentLoop(client, REGISTRY)
    session = Session("window-1")

    result = loop.run(session, "你好")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.OUTPUT_PARSE_FAILED.value
    assert result.turn.is_failed
    assert not result.recorded
    assert result.malformed_retries == 1
    assert len(client.requests) == 2
    assert session.turn_count == 0


def test_native_parse_error_from_client_is_treated_as_malformed() -> None:
    client = MockLLMClient(
        [
            ParseError(
                "一次响应只能包含一个工具调用",
                {"reason": "multiple_tool_calls"},
            ),
            final("好"),
        ]
    )
    loop = AgentLoop(client, REGISTRY)

    result = loop.run(Session("window-1"), "你好")

    assert result.ok
    assert result.malformed_retries == 1
    assert "multiple_tool_calls" in client.requests[1].messages[-1].content


# ---------------------------------------------------------------------------
# limits and tool failures
# ---------------------------------------------------------------------------


def test_tool_call_limit_stops_before_extra_execution() -> None:
    client = MockLLMClient(
        [tool_call("calculator", {"expression": "1+1"}) for _ in range(3)]
    )
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink, max_tool_calls=2)
    session = Session("window-1")

    result = loop.run(session, "一直算")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_LIMIT_EXCEEDED.value
    assert result.error.details["max_tool_calls"] == 2
    assert result.error.details["attempted_tool"] == "calculator"
    assert result.turn.is_failed
    assert len(result.turn.tool_exchanges) == 2
    assert len(sink.traces) == 2
    assert len(client.requests) == 3
    assert session.turn_count == 0


@pytest.mark.parametrize(
    ("raw_call", "expected_code"),
    [
        (tool_call("weather", {}), ErrorCode.TOOL_NOT_FOUND.value),
        (
            tool_call("calculator", {}),
            ErrorCode.INVALID_TOOL_ARGUMENTS.value,
        ),
        (
            tool_call("calculator", {"expression": "1/0"}),
            ErrorCode.TOOL_EXECUTION_FAILED.value,
        ),
    ],
)
def test_tool_failures_are_fed_back_and_traced(
    raw_call: str,
    expected_code: str,
) -> None:
    client = MockLLMClient([raw_call, final("已看到工具错误")])
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink)
    session = Session("window-1")

    result = loop.run(session, "调用工具")

    assert result.ok
    assert result.tool_calls == 1
    exchange = result.turn.tool_exchanges[0]
    assert not exchange.tool_result.ok
    assert exchange.tool_result.error is not None
    assert exchange.tool_result.error.code == expected_code

    trace = sink.traces[0]
    assert trace.status == "failure"
    assert trace.error is not None
    assert trace.error.code == expected_code

    tool_message = [
        m for m in client.requests[1].messages if m.role is Role.TOOL
    ][-1]
    assert expected_code in tool_message.content


# ---------------------------------------------------------------------------
# model / summarizer failures
# ---------------------------------------------------------------------------


def test_model_request_failure_terminates_turn() -> None:
    client = MockLLMClient([ModelError("接口不可用", {"status": 502})])
    sink = MemoryTraceSink()
    loop = AgentLoop(client, REGISTRY, trace_sink=sink)
    session = Session("window-1")

    result = loop.run(session, "你好")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.MODEL_REQUEST_FAILED.value
    assert result.error.details["status"] == 502
    assert result.turn.is_failed
    assert not result.recorded
    assert session.turn_count == 0
    assert sink.traces == ()


def test_unexpected_client_exception_is_wrapped_without_leaking_text() -> None:
    client = MockLLMClient([RuntimeError("boom sk-test-secret")])
    loop = AgentLoop(client, REGISTRY)

    result = loop.run(Session("window-1"), "你好")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.MODEL_REQUEST_FAILED.value
    assert result.error.details["cause_type"] == "RuntimeError"
    assert "sk-test-secret" not in json.dumps(result.error.to_dict())


def test_summarizer_failure_stops_turn_and_keeps_history() -> None:
    session = Session(
        "window-1",
        summarizer=ExplodingSummarizer(),
        max_turns=2,
        recent_turns=1,
    )
    client = MockLLMClient([final("a"), final("b"), final("c")])
    loop = AgentLoop(client, REGISTRY)

    first = loop.run(session, "q1")
    second = loop.run(session, "q2")
    third = loop.run(session, "q3")

    assert first.ok and second.ok
    assert not third.ok
    assert third.error is not None
    assert third.error.code == ErrorCode.SUMMARIZER_FAILED.value
    assert not third.recorded
    assert third.turn.is_completed
    assert third.turn.answer == "c"
    assert third.answer is None
    assert session.turn_count == 2
    assert session.summary is None
    assert [turn.user_input for turn in session.turns] == ["q1", "q2"]


def test_trace_sink_failure_does_not_raise_or_keep_open_turn() -> None:
    client = MockLLMClient([tool_call("calculator", {"expression": "1+1"})])
    loop = AgentLoop(client, REGISTRY, trace_sink=BrokenTraceSink())

    result = loop.run(Session("window-1"), "算一下")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.AGENT_ERROR.value
    assert result.error.details["cause_type"] == "OSError"
    assert result.turn.is_failed


# ---------------------------------------------------------------------------
# session isolation and input validation
# ---------------------------------------------------------------------------


def test_loop_keeps_session_histories_separate() -> None:
    client = MockLLMClient([final("一号"), final("二号"), final("一号续")])
    loop = AgentLoop(client, REGISTRY)
    window_1 = Session("window-1")
    window_2 = Session("window-2")

    loop.run(window_1, "一号问题")
    loop.run(window_2, "二号问题")
    loop.run(window_1, "继续一号")

    window_2_text = "\n".join(
        message.content for message in client.requests[1].messages
    )
    assert "一号问题" not in window_2_text
    assert "一号" not in window_2_text

    window_1_text = "\n".join(
        message.content for message in client.requests[2].messages
    )
    assert "一号问题" in window_1_text
    assert "二号问题" not in window_1_text
    assert window_1.turn_count == 2
    assert window_2.turn_count == 1


def test_todo_state_is_isolated_between_sessions() -> None:
    client = MockLLMClient(
        [
            tool_call("todo", {"action": "add", "text": "买牛奶"}),
            final("已记录"),
            tool_call("todo", {"action": "list"}),
            final("没有待办"),
        ]
    )
    loop = AgentLoop(client, REGISTRY)
    window_1 = Session("window-1")
    window_2 = Session("window-2")

    loop.run(window_1, "记一下买牛奶")
    loop.run(window_2, "我的待办是什么")

    items_1 = window_1.tool_state_for("todo")["items"]
    items_2 = window_2.tool_state_for("todo")["items"]
    assert [item["text"] for item in items_1] == ["买牛奶"]
    assert items_2 == []

    list_result = [
        m for m in client.requests[3].messages if m.role is Role.TOOL
    ][-1]
    assert '"items": []' in list_result.content


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tool_calls": 0},
        {"max_tool_calls": -1},
        {"max_tool_calls": True},
        {"max_tool_calls": 1.5},
        {"max_malformed_retries": -1},
        {"max_malformed_retries": 1.5},
    ],
)
def test_loop_rejects_invalid_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AgentLoop(MockLLMClient([]), REGISTRY, **kwargs)


def test_loop_rejects_invalid_dependencies() -> None:
    with pytest.raises(ValueError):
        AgentLoop(object(), REGISTRY)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(MockLLMClient([]), REGISTRY, trace_sink=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(MockLLMClient([]), REGISTRY, executor=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentLoop(
            MockLLMClient([]),
            REGISTRY,
            executor=ToolExecutor(ToolRegistry()),
        )


def test_run_rejects_invalid_input() -> None:
    loop = AgentLoop(MockLLMClient([]), REGISTRY)

    with pytest.raises(ValueError):
        loop.run(Session("window-1"), "   ")
    with pytest.raises(ValueError):
        loop.run(object(), "你好")  # type: ignore[arg-type]

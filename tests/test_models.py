import uuid
from datetime import datetime, timezone

import pytest

from min_agent.errors import ErrorCode, ErrorInfo
from min_agent.models import (
    DecisionKind,
    Message,
    ModelDecision,
    Role,
    ToolCall,
    ToolExchange,
    ToolResult,
    Turn,
    TurnStatus,
)


def make_call(call_id: str = "call-1", name: str = "calculator") -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments={"expression": "1+1"})


def make_result(call_id: str = "call-1", name: str = "calculator") -> ToolResult:
    return ToolResult.success(call_id=call_id, name=name, output=2)


def test_message_normalizes_known_roles() -> None:
    message = Message(role="user", content="你好")

    assert message.role is Role.USER
    assert message.content == "你好"
    assert message.tool_call_id is None


def test_message_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="role"):
        Message(role="narrator", content="hello")


def test_tool_message_requires_call_id() -> None:
    with pytest.raises(ValueError, match="tool_call_id"):
        Message(role=Role.TOOL, content="{}")

    message = Message(role=Role.TOOL, content="{}", tool_call_id="call-1", name="search")
    assert message.tool_call_id == "call-1"


def test_non_tool_message_must_not_carry_call_id() -> None:
    with pytest.raises(ValueError, match="only tool messages"):
        Message(role=Role.ASSISTANT, content="ok", tool_call_id="call-1")


def test_message_requires_string_content() -> None:
    with pytest.raises(ValueError, match="content"):
        Message(role=Role.USER, content=None)  # type: ignore[arg-type]


def test_tool_call_normalizes_name_and_copies_arguments() -> None:
    arguments = {"query": "pytest"}
    call = ToolCall(call_id="call-1", name="  search  ", arguments=arguments)

    arguments["query"] = "changed"

    assert call.name == "search"
    assert call.arguments == {"query": "pytest"}
    assert call.to_dict() == {
        "call_id": "call-1",
        "name": "search",
        "arguments": {"query": "pytest"},
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"call_id": "", "name": "search"},
        {"call_id": "call-1", "name": "   "},
        {"call_id": "call-1", "name": "search", "arguments": ["not", "dict"]},
        {"call_id": "call-1", "name": "search", "arguments": {1: "bad-key"}},
    ],
)
def test_tool_call_rejects_invalid_shape(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ToolCall(**kwargs)


def test_tool_result_success_is_explicit() -> None:
    result = ToolResult.success("call-1", "calculator", output=2)

    assert result.ok is True
    assert result.error is None
    assert result.error_code is None
    assert result.to_dict() == {
        "call_id": "call-1",
        "name": "calculator",
        "ok": True,
        "output": 2,
        "error": None,
    }


def test_tool_result_failure_carries_structured_error() -> None:
    error = ErrorInfo(ErrorCode.TOOL_EXECUTION_FAILED, "division by zero")
    result = ToolResult.failure("call-1", "calculator", error)

    assert result.ok is False
    assert result.error == error
    assert result.error_code == "tool_execution_failed"
    assert result.output is None
    assert result.to_dict()["error"] == error.to_dict()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"call_id": "call-1", "name": "calculator", "ok": True, "error": ErrorInfo("x", "y")},
        {"call_id": "call-1", "name": "calculator", "ok": False},
        {
            "call_id": "call-1",
            "name": "calculator",
            "ok": False,
            "output": 0,
            "error": ErrorInfo("x", "y"),
        },
        {"call_id": "call-1", "name": "calculator", "ok": "yes"},
    ],
)
def test_tool_result_rejects_ambiguous_states(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ToolResult(**kwargs)


def test_decision_tool_call_keeps_thought_summary() -> None:
    call = make_call()
    decision = ModelDecision.from_tool_call(call, thought_summary="需要先算一下")

    assert decision.kind is DecisionKind.TOOL_CALL
    assert decision.is_tool_call is True
    assert decision.is_final is False
    assert decision.tool_call == call
    assert decision.answer is None
    assert decision.thought_summary == "需要先算一下"


def test_decision_final_keeps_thought_summary() -> None:
    decision = ModelDecision.from_final("结果是 2", thought_summary="已有结果")

    assert decision.kind is DecisionKind.FINAL
    assert decision.is_final is True
    assert decision.is_tool_call is False
    assert decision.answer == "结果是 2"
    assert decision.tool_call is None
    assert decision.thought_summary == "已有结果"


def test_decision_accepts_string_kind_and_blank_thought() -> None:
    decision = ModelDecision(kind="final", answer="ok", thought_summary="   ")

    assert decision.kind is DecisionKind.FINAL
    assert decision.thought_summary is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": DecisionKind.TOOL_CALL},
        {"kind": DecisionKind.TOOL_CALL, "tool_call": make_call(), "answer": "extra"},
        {"kind": DecisionKind.FINAL},
        {"kind": DecisionKind.FINAL, "answer": "   "},
        {"kind": DecisionKind.FINAL, "answer": "ok", "tool_call": make_call()},
        {"kind": "unknown", "answer": "ok"},
    ],
)
def test_decision_rejects_invalid_shapes(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ModelDecision(**kwargs)


def test_tool_exchange_requires_matching_call_and_result() -> None:
    exchange = ToolExchange(
        tool_call=make_call(),
        tool_result=make_result(),
        thought_summary="先计算",
    )
    assert exchange.thought_summary == "先计算"

    with pytest.raises(ValueError, match="call_id"):
        ToolExchange(tool_call=make_call("call-1"), tool_result=make_result("call-2"))

    with pytest.raises(ValueError, match="name"):
        ToolExchange(
            tool_call=make_call(name="calculator"),
            tool_result=make_result(name="search"),
        )


def test_new_turn_starts_open() -> None:
    turn = Turn(session_id="window-1", user_input="帮我算 1+1")

    assert turn.status is TurnStatus.OPEN
    assert turn.is_open is True
    assert turn.is_completed is False
    assert turn.is_failed is False
    assert turn.answer is None
    assert turn.error is None
    assert turn.tool_exchanges == []
    assert turn.created_at.tzinfo == timezone.utc
    uuid.UUID(turn.turn_id)


def test_turn_records_complete_tool_exchange_before_answer() -> None:
    turn = Turn(session_id="window-1", user_input="帮我算 1+1")
    exchange = turn.add_exchange(make_call(), make_result(), thought_summary="需要计算")

    assert turn.tool_exchanges == [exchange]
    assert turn.is_open is True

    turn.complete("结果是 2")

    assert turn.is_completed is True
    assert turn.answer == "结果是 2"
    assert turn.tool_exchanges[0].tool_call.name == "calculator"
    assert turn.tool_exchanges[0].tool_result.output == 2


def test_finished_turn_rejects_further_changes() -> None:
    completed = Turn(session_id="window-1", user_input="hi")
    completed.complete("hello")

    with pytest.raises(ValueError, match="finished turn"):
        completed.add_exchange(make_call(), make_result())
    with pytest.raises(ValueError, match="finished turn"):
        completed.complete("again")
    with pytest.raises(ValueError, match="finished turn"):
        completed.fail(ErrorInfo("x", "y"))


def test_failed_turn_keeps_history_and_error() -> None:
    turn = Turn(session_id="window-1", user_input="帮我算 1+1")
    turn.add_exchange(make_call(), make_result())
    error = ErrorInfo(ErrorCode.SUMMARIZER_FAILED, "summary unavailable")

    turn.fail(error)

    assert turn.is_failed is True
    assert turn.status is TurnStatus.FAILED
    assert turn.error == error
    assert turn.answer is None
    assert len(turn.tool_exchanges) == 1

    with pytest.raises(ValueError, match="finished turn"):
        turn.complete("late answer")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_id": "", "user_input": "hi"},
        {"session_id": "window-1", "user_input": "   "},
        {"session_id": "window-1", "user_input": "hi", "answer": "prefilled"},
        {"session_id": "window-1", "user_input": "hi", "status": TurnStatus.COMPLETED},
        {
            "session_id": "window-1",
            "user_input": "hi",
            "created_at": datetime(2026, 1, 1),
        },
    ],
)
def test_turn_rejects_invalid_or_inconsistent_state(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        Turn(**kwargs)


def test_turn_ids_are_unique() -> None:
    first = Turn(session_id="window-1", user_input="hi")
    second = Turn(session_id="window-1", user_input="hi")

    assert first.turn_id != second.turn_id

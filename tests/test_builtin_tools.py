"""P2 三个必备工具的功能、错误与隔离测试。"""

from __future__ import annotations

import builtins
import json
import socket

import pytest

from min_agent.builtin_tools import (
    _CalcError,
    create_default_registry,
    evaluate_expression,
)
from min_agent.errors import ErrorCode
from min_agent.models import ToolCall
from min_agent.session import Session, SessionManager
from min_agent.tools import ToolExecutor


@pytest.fixture()
def executor() -> ToolExecutor:
    return ToolExecutor(create_default_registry())


def call(name: str, call_id: str = "call-1", **arguments) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+2*3", 7),
        ("(1+2)*3", 9),
        ("10/4", 2.5),
        ("10//4", 2),
        ("10%4", 2),
        ("2**10", 1024),
        ("-2**2", -4),
        ("2**3**2", 512),
        ("2**-1", 0.5),
        (".5", 0.5),
        ("1.5+2.5", 4.0),
        ("--2", 2),
        ("2*-3", -6),
        ("1 + 2", 3),
        ("(((1+2)))", 3),
    ],
)
def test_calculator_evaluates_valid_expressions(
    executor: ToolExecutor,
    expression: str,
    expected: int | float,
) -> None:
    result = executor.execute(call("calculator", expression=expression))

    assert result.ok is True
    assert result.output == {"expression": expression, "result": expected}
    assert isinstance(result.output["result"], (int, float))


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("1/0", "division_by_zero"),
        ("5//0", "division_by_zero"),
        ("5%0", "division_by_zero"),
        ("0**-1", "division_by_zero"),
        ("1+", "invalid_expression"),
        ("abc", "invalid_expression"),
        ("2(3)", "invalid_expression"),
        ("1..2", "invalid_expression"),
        ("__import__('os')", "invalid_expression"),
        ("9**9999", "exponent_too_large"),
        ("(10**1000)**1000", "result_too_large"),
        ("1" * 51, "number_too_large"),
    ],
)
def test_calculator_returns_structured_failures(
    executor: ToolExecutor,
    expression: str,
    reason: str,
) -> None:
    result = executor.execute(call("calculator", expression=expression))

    assert result.ok is False
    assert result.output is None
    assert result.error_code == ErrorCode.TOOL_EXECUTION_FAILED
    assert result.error.details["reason"] == reason
    assert result.error.details["expression"] == expression


def test_calculator_schema_rejects_overlong_expression(
    executor: ToolExecutor,
) -> None:
    result = executor.execute(call("calculator", expression="1+" * 101))

    assert result.ok is False
    assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS
    assert "at most 200" in result.error.details["errors"][0]["reason"]


def test_calculator_helper_rejects_overlong_expression_directly() -> None:
    with pytest.raises(_CalcError) as excinfo:
        evaluate_expression("1+" * 101)

    assert excinfo.value.reason == "expression_too_long"


def test_calculator_never_calls_eval(
    executor: ToolExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_eval(*args, **kwargs):
        raise AssertionError("calculator must not use eval")

    monkeypatch.setattr(builtins, "eval", forbidden_eval)

    result = executor.execute(call("calculator", expression="6*7"))

    assert result.ok is True
    assert result.output["result"] == 42


def test_calculator_rejects_bad_arguments(executor: ToolExecutor) -> None:
    missing = executor.execute(call("calculator"))
    wrong_type = executor.execute(call("calculator", expression=1))
    blank = executor.execute(call("calculator", expression="   "))

    for result in (missing, wrong_type, blank):
        assert result.ok is False
        assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS


# ---------------------------------------------------------------------------
# mock search
# ---------------------------------------------------------------------------


def test_search_returns_stable_fixture_for_fixed_query(executor: ToolExecutor) -> None:
    first = executor.execute(call("search", query="杭州天气"))
    second = executor.execute(call("search", query="杭州天气"))

    assert first.ok is True
    assert first.output == second.output
    assert first.output["source"] == "mock"
    assert first.output["query"] == "杭州天气"
    assert first.output["total"] == 2
    assert first.output["returned"] == 2
    assert first.output["results"][0]["url"] == "https://mock.local/weather/hangzhou"
    json.dumps(first.output, ensure_ascii=False)


def test_search_normalizes_whitespace_and_case_for_fixtures(
    executor: ToolExecutor,
) -> None:
    result = executor.execute(call("search", query="  PYTHON   pytest  "))

    assert result.ok is True
    assert result.output["query"] == "PYTHON pytest"
    assert result.output["results"][0]["title"] == "pytest 官方文档"


def test_search_fallback_is_deterministic_and_respects_limit(
    executor: ToolExecutor,
) -> None:
    first = executor.execute(
        call("search", query="一个数据库里不存在的查询", limit=2)
    )
    second = executor.execute(
        call("search", query="一个数据库里不存在的查询", limit=2)
    )

    assert first.output == second.output
    assert first.output["total"] == 5
    assert first.output["returned"] == 2
    assert len(first.output["results"]) == 2
    digest_path = first.output["results"][0]["url"].split("/")[4]
    assert digest_path in first.output["results"][1]["url"]


@pytest.mark.parametrize("limit", [1, 3, 5])
def test_search_limit_bounds(executor: ToolExecutor, limit: int) -> None:
    result = executor.execute(call("search", query="杭州天气", limit=limit))

    assert result.ok is True
    assert result.output["returned"] == min(limit, 2)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": "杭州天气", "limit": 0},
        {"query": "杭州天气", "limit": 6},
        {"query": "杭州天气", "limit": "3"},
        {"query": "杭州天气", "extra": True},
    ],
)
def test_search_rejects_bad_arguments(
    executor: ToolExecutor,
    arguments: dict,
) -> None:
    result = executor.execute(call("search", **arguments))

    assert result.ok is False
    assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS


def test_search_rejects_blank_query(executor: ToolExecutor) -> None:
    result = executor.execute(call("search", query="   "))

    assert result.ok is False
    assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS


def test_search_does_not_touch_the_network(
    executor: ToolExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("mock search must not use the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)

    result = executor.execute(call("search", query="杭州天气"))

    assert result.ok is True


# ---------------------------------------------------------------------------
# todo
# ---------------------------------------------------------------------------


def test_todo_add_list_complete_flow(executor: ToolExecutor) -> None:
    session = Session("window-1")

    added_milk = executor.execute(
        call("todo", action="add", text="买牛奶"), session=session
    )
    added_report = executor.execute(
        call("todo", action="add", text="写周报"), session=session
    )
    listed = executor.execute(call("todo", action="list"), session=session)
    completed = executor.execute(
        call("todo", action="complete", id=1), session=session
    )
    listed_again = executor.execute(call("todo", action="list"), session=session)

    assert added_milk.output["item"] == {"id": 1, "text": "买牛奶", "done": False}
    assert added_report.output["item"] == {"id": 2, "text": "写周报", "done": False}
    assert listed.output == {
        "action": "list",
        "items": [
            {"id": 1, "text": "买牛奶", "done": False},
            {"id": 2, "text": "写周报", "done": False},
        ],
        "total": 2,
    }
    assert completed.output["item"] == {"id": 1, "text": "买牛奶", "done": True}
    assert completed.output["already_completed"] is False
    assert listed_again.output["items"][0]["done"] is True
    assert listed_again.output["items"][1]["done"] is False


def test_todo_state_is_isolated_between_sessions(executor: ToolExecutor) -> None:
    manager = SessionManager()
    window_1 = manager.create("window-1")
    window_2 = manager.create("window-2")

    executor.execute(call("todo", action="add", text="带伞"), session=window_1)
    executor.execute(call("todo", action="add", text="提交周报"), session=window_2)

    assert manager.switch("window-1") is window_1
    list_1 = executor.execute(
        call("todo", action="list"), session=manager.current
    )
    assert manager.switch("window-2") is window_2
    list_2 = executor.execute(
        call("todo", action="list"), session=manager.current
    )
    assert manager.switch("window-1") is window_1

    assert [item["text"] for item in list_1.output["items"]] == ["带伞"]
    assert [item["text"] for item in list_2.output["items"]] == ["提交周报"]
    assert window_1.tool_state_for("todo")["next_id"] == 2
    assert window_2.tool_state_for("todo")["next_id"] == 2


def test_todo_complete_unknown_item_returns_explicit_error(
    executor: ToolExecutor,
) -> None:
    session = Session("window-1")

    result = executor.execute(
        call("todo", action="complete", id=99), session=session
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.TOOL_EXECUTION_FAILED
    assert result.error.details == {"reason": "todo_not_found", "todo_id": 99}


def test_todo_complete_twice_is_idempotent(executor: ToolExecutor) -> None:
    session = Session("window-1")
    executor.execute(call("todo", action="add", text="买牛奶"), session=session)

    first = executor.execute(call("todo", action="complete", id=1), session=session)
    second = executor.execute(call("todo", action="complete", id=1), session=session)

    assert first.output["already_completed"] is False
    assert second.ok is True
    assert second.output["already_completed"] is True
    assert second.output["item"]["done"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "unknown"},
        {"action": "add"},
        {"action": "add", "text": "   "},
        {"action": "add", "text": "x" * 201},
        {"action": "complete"},
        {"action": "list", "extra": 1},
    ],
)
def test_todo_rejects_bad_arguments(
    executor: ToolExecutor,
    arguments: dict,
) -> None:
    result = executor.execute(
        call("todo", **arguments), session=Session("window-1")
    )

    assert result.ok is False
    assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS


def test_todo_requires_session_without_creating_state(
    executor: ToolExecutor,
) -> None:
    result = executor.execute(call("todo", action="list"))

    assert result.ok is False
    assert result.error_code == ErrorCode.TOOL_EXECUTION_FAILED
    assert result.error.details["reason"] == "missing_session"


def test_todo_list_on_empty_session(executor: ToolExecutor) -> None:
    result = executor.execute(
        call("todo", action="list"), session=Session("window-1")
    )

    assert result.output == {"action": "list", "items": [], "total": 0}


def test_todo_output_is_detached_from_stored_state(executor: ToolExecutor) -> None:
    session = Session("window-1")
    executor.execute(call("todo", action="add", text="买牛奶"), session=session)

    listed = executor.execute(call("todo", action="list"), session=session)
    listed.output["items"][0]["done"] = True
    listed.output["items"].clear()

    listed_again = executor.execute(call("todo", action="list"), session=session)

    assert listed_again.output["items"] == [
        {"id": 1, "text": "买牛奶", "done": False}
    ]


def test_todo_ids_increment_after_compaction_unrelated_state(
    executor: ToolExecutor,
) -> None:
    session = Session("window-1")
    session.tool_state_for("search")["last_query"] = "杭州天气"

    executor.execute(call("todo", action="add", text="第一条"), session=session)
    executor.execute(call("todo", action="add", text="第二条"), session=session)

    assert session.tool_state_for("todo")["next_id"] == 3
    assert session.tool_state_for("search") == {"last_query": "杭州天气"}

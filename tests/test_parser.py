"""P3 输出解析测试：合法决策、严格失败分支与工具 Schema 校验。"""

from __future__ import annotations

import json

import pytest

from min_agent.builtin_tools import create_default_registry
from min_agent.errors import ErrorCode, ParseError, ToolArgumentError
from min_agent.parser import parse_decision

REGISTRY = create_default_registry()


def test_final_decision_extracts_answer_and_thought() -> None:
    raw = json.dumps(
        {
            "kind": "final",
            "thought_summary": "无需调用工具",
            "answer": "你好，有什么可以帮你？",
        },
        ensure_ascii=False,
    )

    decision = parse_decision(raw, registry=REGISTRY)

    assert decision.is_final
    assert decision.answer == "你好，有什么可以帮你？"
    assert decision.thought_summary == "无需调用工具"
    assert decision.tool_call is None


def test_tool_call_decision_extracts_name_arguments_and_thought() -> None:
    raw = json.dumps(
        {
            "kind": "tool_call",
            "thought_summary": "需要精确计算",
            "tool_call": {
                "name": "calculator",
                "arguments": {"expression": "(12+8)*3"},
            },
        },
        ensure_ascii=False,
    )

    decision = parse_decision(raw, registry=REGISTRY)

    assert decision.is_tool_call
    assert decision.thought_summary == "需要精确计算"
    assert decision.tool_call is not None
    assert decision.tool_call.name == "calculator"
    assert decision.tool_call.arguments == {"expression": "(12+8)*3"}
    assert decision.tool_call.call_id.startswith("call_")
    assert decision.answer is None


def test_tool_call_keeps_model_supplied_call_id() -> None:
    raw = json.dumps(
        {
            "kind": "tool_call",
            "tool_call": {
                "call_id": "call-from-model",
                "name": "search",
                "arguments": {"query": "杭州天气"},
            },
        },
        ensure_ascii=False,
    )

    decision = parse_decision(raw, registry=REGISTRY)

    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-from-model"


def test_parser_accepts_surrounding_whitespace() -> None:
    raw = '\n  {"kind": "final", "answer": "好的"}  \n'

    decision = parse_decision(raw)

    assert decision.answer == "好的"


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_empty_response_is_a_parse_error(raw: str) -> None:
    with pytest.raises(ParseError) as info:
        parse_decision(raw)

    assert info.value.code is ErrorCode.OUTPUT_PARSE_FAILED
    assert info.value.details["reason"] == "empty_response"


def test_invalid_json_is_not_treated_as_final_answer() -> None:
    raw = "答案是 42，但这不是 JSON"

    with pytest.raises(ParseError) as info:
        parse_decision(raw)

    assert info.value.details["reason"] == "invalid_json"
    assert "response" in info.value.details


@pytest.mark.parametrize("raw", ["[]", '"hello"', "null", "42"])
def test_non_object_response_is_rejected(raw: str) -> None:
    with pytest.raises(ParseError) as info:
        parse_decision(raw)

    assert info.value.details["reason"] == "not_an_object"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({}, "missing_field"),
        ({"answer": "hi"}, "missing_field"),
        ({"kind": "unknown", "answer": "hi"}, "unknown_kind"),
        ({"kind": 123, "answer": "hi"}, "unknown_kind"),
        ({"kind": "final"}, "missing_field"),
        ({"kind": "final", "answer": ""}, "invalid_field"),
        ({"kind": "final", "answer": 123}, "invalid_field"),
        ({"kind": "final", "answer": "hi", "thought_summary": 123}, "invalid_field"),
        (
            {
                "kind": "final",
                "answer": "hi",
                "tool_call": {"name": "calculator", "arguments": {}},
            },
            "conflicting_fields",
        ),
        ({"kind": "tool_call"}, "missing_field"),
        ({"kind": "tool_call", "tool_call": "calculator"}, "invalid_field"),
        ({"kind": "tool_call", "tool_call": {}}, "missing_field"),
        (
            {"kind": "tool_call", "tool_call": {"name": "calculator"}},
            "missing_field",
        ),
        (
            {"kind": "tool_call", "tool_call": {"name": 123, "arguments": {}}},
            "invalid_field",
        ),
        (
            {
                "kind": "tool_call",
                "tool_call": {"name": "calculator", "arguments": []},
            },
            "invalid_field",
        ),
        (
            {
                "kind": "tool_call",
                "tool_call": {
                    "name": "calculator",
                    "arguments": {"expression": "1+1"},
                },
                "answer": "结果是 2",
            },
            "conflicting_fields",
        ),
    ],
)
def test_malformed_payloads_raise_parse_error(
    payload: dict,
    reason: str,
) -> None:
    with pytest.raises(ParseError) as info:
        parse_decision(json.dumps(payload, ensure_ascii=False))

    assert info.value.details["reason"] == reason


def test_unknown_tool_is_rejected_when_registry_is_supplied() -> None:
    raw = json.dumps(
        {
            "kind": "tool_call",
            "tool_call": {"name": "weather", "arguments": {}},
        }
    )

    with pytest.raises(ParseError) as info:
        parse_decision(raw, registry=REGISTRY)

    assert info.value.details["reason"] == "unknown_tool"
    assert info.value.details["tool"] == "weather"


def test_tool_is_not_validated_without_registry() -> None:
    raw = json.dumps(
        {
            "kind": "tool_call",
            "tool_call": {"name": "weather", "arguments": {}},
        }
    )

    decision = parse_decision(raw)

    assert decision.tool_call is not None
    assert decision.tool_call.name == "weather"


@pytest.mark.parametrize(
    ("name", "arguments", "expected_path"),
    [
        ("calculator", {}, "$.expression"),
        ("calculator", {"expression": 123}, "$.expression"),
        ("search", {"query": "ok", "limit": 99}, "$.limit"),
        ("search", {"query": "ok", "extra": True}, "$.extra"),
        ("todo", {"action": "delete"}, "$.action"),
    ],
)
def test_tool_arguments_must_match_schema(
    name: str,
    arguments: dict,
    expected_path: str,
) -> None:
    raw = json.dumps(
        {
            "kind": "tool_call",
            "tool_call": {"name": name, "arguments": arguments},
        }
    )

    with pytest.raises(ToolArgumentError) as info:
        parse_decision(raw, registry=REGISTRY)

    assert info.value.code is ErrorCode.INVALID_TOOL_ARGUMENTS
    assert info.value.details["tool"] == name
    paths = [issue["path"] for issue in info.value.details["errors"]]
    assert expected_path in paths


def test_parse_decision_rejects_non_string_response() -> None:
    with pytest.raises(ValueError):
        parse_decision(None)  # type: ignore[arg-type]

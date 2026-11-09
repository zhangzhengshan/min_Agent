"""P2 Tool Registry、Tool Executor 与参数校验测试。"""

from __future__ import annotations

import json

import pytest

from min_agent.builtin_tools import calculator_tool, create_default_registry
from min_agent.errors import (
    DuplicateToolError,
    ErrorCode,
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
)
from min_agent.models import ToolCall
from min_agent.session import Session
from min_agent.tools import (
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    validate_arguments,
)

ECHO_PARAMETERS = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def echo_handler(arguments, context):
    return {
        "text": arguments["text"],
        "session_id": context.session.session_id if context.session else None,
    }


def make_spec(
    handler=echo_handler,
    *,
    name: str = "echo",
    description: str = "回显文本",
    parameters: dict | None = None,
    requires_session: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters=ECHO_PARAMETERS if parameters is None else parameters,
        handler=handler,
        requires_session=requires_session,
    )


def make_executor(handler=echo_handler, **kwargs) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_spec(handler, **kwargs))
    return ToolExecutor(registry)


def call(arguments: dict, *, name: str = "echo", call_id: str = "call-1") -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_default_registry_exposes_all_three_complete_schemas() -> None:
    registry = create_default_registry()

    assert registry.names == ("calculator", "search", "todo")
    assert len(registry) == 3
    assert "calculator" in registry
    assert "search" in registry
    assert "todo" in registry

    schemas = registry.schemas()
    assert [schema["name"] for schema in schemas] == [
        "calculator",
        "search",
        "todo",
    ]
    for schema in schemas:
        assert set(schema) == {"name", "description", "parameters"}
        assert schema["description"].strip()
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"]["properties"]
    # P3 的请求构造器需要能直接序列化这些 Schema。
    json.dumps(schemas, ensure_ascii=False)


def test_schema_for_returns_a_defensive_copy() -> None:
    registry = create_default_registry()
    schema = registry.schema_for("calculator")
    schema["parameters"]["properties"].clear()

    assert registry.schema_for("calculator")["parameters"]["properties"]


def test_duplicate_registration_is_rejected() -> None:
    registry = create_default_registry()

    with pytest.raises(DuplicateToolError) as excinfo:
        registry.register(calculator_tool())

    assert excinfo.value.code is ErrorCode.DUPLICATE_TOOL
    assert excinfo.value.details == {"tool": "calculator"}
    assert len(registry) == 3


def test_unknown_tool_lookup_is_explicit() -> None:
    registry = create_default_registry()

    with pytest.raises(ToolNotFoundError) as excinfo:
        registry.get("missing")

    assert excinfo.value.code is ErrorCode.TOOL_NOT_FOUND
    assert excinfo.value.details == {"tool": "missing"}


def test_register_rejects_non_spec() -> None:
    with pytest.raises(ValueError, match="ToolSpec"):
        ToolRegistry().register(object())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "bad name"},
        {"name": "a" * 65},
        {"description": "   "},
        {"parameters": []},
        {"parameters": {"type": "array", "items": {"type": "string"}}},
        {"parameters": {"type": "object", "properties": {}, "required": ["x"]}},
        {
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "strng"}},
            }
        },
        {"handler": 1},
        {"requires_session": "yes"},
    ],
)
def test_invalid_tool_specs_are_rejected(kwargs: dict) -> None:
    base = {
        "name": "echo",
        "description": "回显文本",
        "parameters": ECHO_PARAMETERS,
        "handler": echo_handler,
    }
    base.update(kwargs)

    with pytest.raises(ValueError):
        ToolSpec(**base)


def test_tool_spec_copies_parameters_on_creation() -> None:
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    spec = make_spec(parameters=parameters)
    parameters["properties"].clear()

    assert spec.parameters["properties"]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def test_success_returns_unified_result_with_call_identity() -> None:
    result = make_executor().execute(call({"text": "你好"}, call_id="call-9"))

    assert result.ok is True
    assert result.call_id == "call-9"
    assert result.name == "echo"
    assert result.output == {"text": "你好", "session_id": None}
    assert result.error is None


def test_unknown_tool_becomes_failed_result() -> None:
    result = make_executor().execute(call({"text": "hi"}, name="missing"))

    assert result.ok is False
    assert result.output is None
    assert result.error_code == "tool_not_found"
    assert result.error.details == {"tool": "missing"}


def test_missing_and_wrong_arguments_become_invalid_arguments_result() -> None:
    executor = make_executor()

    missing = executor.execute(call({}))
    wrong = executor.execute(call({"text": 1}))
    extra = executor.execute(call({"text": "ok", "extra": True}))

    for result in (missing, wrong, extra):
        assert result.ok is False
        assert result.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS
    assert missing.error.details["errors"] == [
        {"path": "$.text", "reason": "missing required argument"}
    ]
    assert wrong.error.details["errors"][0]["path"] == "$.text"
    assert extra.error.details["errors"] == [
        {"path": "$.extra", "reason": "unexpected argument"}
    ]


def test_handler_exception_is_caught_and_reported() -> None:
    def boom(arguments, context):
        raise RuntimeError("kaboom")

    result = make_executor(boom).execute(call({"text": "hi"}))

    assert result.ok is False
    assert result.error_code == ErrorCode.TOOL_EXECUTION_FAILED
    assert result.error.details["cause_type"] == "RuntimeError"
    assert result.error.details["message"] == "kaboom"
    assert result.output is None


def test_structured_tool_errors_keep_their_code_and_details() -> None:
    def fail(arguments, context):
        raise ToolExecutionError("算不了", {"reason": "not_supported"})

    def reject(arguments, context):
        raise ToolArgumentError("参数不对", {"argument": "text"})

    failed = make_executor(fail).execute(call({"text": "hi"}))
    rejected = make_executor(reject).execute(call({"text": "hi"}))

    assert failed.error_code == ErrorCode.TOOL_EXECUTION_FAILED
    assert failed.error.details == {"reason": "not_supported"}
    assert rejected.error_code == ErrorCode.INVALID_TOOL_ARGUMENTS
    assert rejected.error.details == {"argument": "text"}


def test_requires_session_is_enforced_before_handler_runs() -> None:
    executor = make_executor(requires_session=True)

    missing = executor.execute(call({"text": "hi"}))
    session = Session("window-1")
    provided = executor.execute(call({"text": "hi"}), session=session)

    assert missing.ok is False
    assert missing.error.details["reason"] == "missing_session"
    assert provided.ok is True
    assert provided.output["session_id"] == "window-1"


def test_executor_rejects_invalid_runtime_types() -> None:
    with pytest.raises(ValueError, match="ToolRegistry"):
        ToolExecutor(object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="ToolCall"):
        ToolExecutor(ToolRegistry()).execute(object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Session"):
        make_executor().execute(call({"text": "hi"}), session=object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Session"):
        ToolContext(session=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Schema validation helper
# ---------------------------------------------------------------------------

NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 4},
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "mode": {"type": "string", "enum": ["a", "b"]},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
    },
    "required": ["name", "count", "mode"],
    "additionalProperties": False,
}


def test_validator_accepts_valid_nested_arguments() -> None:
    arguments = {"name": "ab", "count": 1, "mode": "a", "tags": ["x"]}

    assert validate_arguments(NESTED_SCHEMA, arguments) == []


def test_validator_collects_all_missing_required_fields() -> None:
    issues = validate_arguments(NESTED_SCHEMA, {})

    assert [issue["path"] for issue in issues] == [
        "$.name",
        "$.count",
        "$.mode",
    ]
    assert all(issue["reason"] == "missing required argument" for issue in issues)


@pytest.mark.parametrize(
    ("arguments", "path", "reason_part"),
    [
        ({"name": "a", "count": 1, "mode": "a"}, "$.name", "at least 2"),
        ({"name": "abcde", "count": 1, "mode": "a"}, "$.name", "at most 4"),
        ({"name": "ab", "count": 0, "mode": "a"}, "$.count", "greater than or equal"),
        ({"name": "ab", "count": 4, "mode": "a"}, "$.count", "less than or equal"),
        ({"name": "ab", "count": True, "mode": "a"}, "$.count", "expected integer"),
        ({"name": "ab", "count": 1, "mode": "c"}, "$.mode", "allowed enum"),
        ({"name": "ab", "count": 1, "mode": "a", "tags": []}, "$.tags", "at least 1"),
        (
            {"name": "ab", "count": 1, "mode": "a", "tags": ["x", "y", "z"]},
            "$.tags",
            "at most 2",
        ),
        (
            {"name": "ab", "count": 1, "mode": "a", "tags": ["x", 1]},
            "$.tags[1]",
            "expected string",
        ),
        (
            {"name": "ab", "count": 1, "mode": "a", "extra": 1},
            "$.extra",
            "unexpected argument",
        ),
    ],
)
def test_validator_reports_nested_problems(
    arguments: dict,
    path: str,
    reason_part: str,
) -> None:
    issues = validate_arguments(NESTED_SCHEMA, arguments)

    assert any(
        issue["path"] == path and reason_part in issue["reason"]
        for issue in issues
    ), issues


def test_validator_treats_integer_as_number_but_not_boolean() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "number"}},
        "required": ["value"],
    }

    assert validate_arguments(schema, {"value": 3}) == []
    assert validate_arguments(schema, {"value": 3.5}) == []
    assert validate_arguments(schema, {"value": True})[0]["path"] == "$.value"


def test_validator_rejects_non_object_root() -> None:
    issues = validate_arguments(ECHO_PARAMETERS, ["not", "an", "object"])

    assert issues == [
        {"path": "$", "reason": "expected object, got array"}
    ]


def test_validator_rejects_non_mapping_schema() -> None:
    with pytest.raises(ValueError, match="mapping"):
        validate_arguments([], {})  # type: ignore[arg-type]

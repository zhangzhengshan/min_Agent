import pytest

from min_agent.errors import (
    AgentError,
    ConfigError,
    DuplicateToolError,
    ErrorCode,
    ErrorInfo,
    ModelError,
    ParseError,
    SessionNotFoundError,
    SummarizationError,
    ToolArgumentError,
    ToolExecutionError,
    ToolLimitError,
    ToolNotFoundError,
)


def test_error_info_builds_serializable_payload() -> None:
    info = ErrorInfo(
        code=ErrorCode.TOOL_NOT_FOUND,
        message="unknown tool",
        details={"tool": "weather"},
    )

    assert info.code == "tool_not_found"
    assert info.to_dict() == {
        "code": "tool_not_found",
        "message": "unknown tool",
        "details": {"tool": "weather"},
    }


def test_error_info_accepts_custom_string_code() -> None:
    info = ErrorInfo(code="custom_code", message="custom")

    assert info.code == "custom_code"
    assert info.details == {}


def test_error_info_copies_details() -> None:
    details = {"tool": "weather"}
    info = ErrorInfo(code=ErrorCode.TOOL_NOT_FOUND, message="missing", details=details)

    details["tool"] = "changed"

    assert info.details == {"tool": "weather"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": ErrorCode.AGENT_ERROR, "message": ""},
        {"code": ErrorCode.AGENT_ERROR, "message": "   "},
        {"code": "", "message": "boom"},
        {"code": ErrorCode.AGENT_ERROR, "message": "boom", "details": ["not", "map"]},
    ],
)
def test_error_info_rejects_invalid_payload(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ErrorInfo(**kwargs)


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (AgentError, ErrorCode.AGENT_ERROR),
        (ConfigError, ErrorCode.CONFIG_INVALID),
        (ModelError, ErrorCode.MODEL_REQUEST_FAILED),
        (ParseError, ErrorCode.OUTPUT_PARSE_FAILED),
        (ToolArgumentError, ErrorCode.INVALID_TOOL_ARGUMENTS),
        (ToolNotFoundError, ErrorCode.TOOL_NOT_FOUND),
        (DuplicateToolError, ErrorCode.DUPLICATE_TOOL),
        (ToolExecutionError, ErrorCode.TOOL_EXECUTION_FAILED),
        (ToolLimitError, ErrorCode.TOOL_LIMIT_EXCEEDED),
        (SummarizationError, ErrorCode.SUMMARIZER_FAILED),
        (SessionNotFoundError, ErrorCode.SESSION_NOT_FOUND),
    ],
)
def test_agent_error_subclasses_expose_stable_codes(
    error_type: type[AgentError],
    expected_code: ErrorCode,
) -> None:
    error = error_type("boom")

    assert error.code is expected_code
    assert error.to_info() == ErrorInfo(code=expected_code, message="boom")
    assert str(error) == f"[{expected_code.value}] boom"


def test_agent_error_is_catchable_and_copies_details() -> None:
    details = {"status": 500}

    with pytest.raises(ModelError) as exc_info:
        raise ModelError("model unavailable", details)

    details["status"] = 0
    error = exc_info.value

    assert isinstance(error, AgentError)
    assert error.details == {"status": 500}
    assert error.to_info().to_dict() == {
        "code": "model_request_failed",
        "message": "model unavailable",
        "details": {"status": 500},
    }


def test_agent_error_rejects_empty_message() -> None:
    with pytest.raises(ValueError):
        AgentError("  ")

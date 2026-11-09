"""P4 trace 测试：字段契约、成功/失败校验与 JSONL 写入。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from min_agent.errors import ErrorCode, ErrorInfo
from min_agent.trace import (
    JsonlTraceSink,
    MemoryTraceSink,
    NullTraceSink,
    ToolTrace,
    TraceSink,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def make_trace(**overrides) -> ToolTrace:
    values = {
        "timestamp": NOW,
        "session_id": "window-1",
        "turn_id": "turn-1",
        "sequence": 1,
        "call_id": "call-1",
        "tool": "calculator",
        "arguments": {"expression": "1+1"},
        "ok": True,
        "duration_ms": 3,
        "error": None,
    }
    values.update(overrides)
    return ToolTrace(**values)


def test_tool_trace_serializes_required_fields() -> None:
    trace = make_trace()

    payload = trace.to_dict()

    assert set(payload) == {
        "timestamp",
        "session_id",
        "turn_id",
        "sequence",
        "call_id",
        "tool",
        "arguments",
        "ok",
        "status",
        "duration_ms",
        "error",
    }
    assert payload["timestamp"] == NOW.isoformat()
    assert payload["session_id"] == "window-1"
    assert payload["turn_id"] == "turn-1"
    assert payload["sequence"] == 1
    assert payload["tool"] == "calculator"
    assert payload["arguments"] == {"expression": "1+1"}
    assert payload["ok"] is True
    assert payload["status"] == "success"
    assert payload["duration_ms"] == 3
    assert payload["error"] is None


def test_failed_trace_carries_structured_error() -> None:
    error = ErrorInfo(
        ErrorCode.TOOL_NOT_FOUND,
        "工具未注册",
        {"tool": "weather"},
    )
    trace = make_trace(ok=False, error=error, tool="weather")

    payload = trace.to_dict()

    assert payload["status"] == "failure"
    assert payload["error"]["code"] == "tool_not_found"
    assert payload["error"]["details"] == {"tool": "weather"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"timestamp": datetime(2026, 11, 9, 12, 0)},  # naive datetime
        {"sequence": 0},
        {"duration_ms": -1},
        {"arguments": ["not", "a", "mapping"]},
        {"ok": "yes"},
        {"ok": True, "error": ErrorInfo("agent_error", "不应存在")},
        {"ok": False, "error": None},
    ],
)
def test_tool_trace_rejects_invalid_records(overrides: dict) -> None:
    with pytest.raises(ValueError):
        make_trace(**overrides)


def test_memory_and_null_sinks() -> None:
    trace = make_trace()
    memory = MemoryTraceSink()
    memory.record(trace)
    assert memory.traces == (trace,)

    memory.clear()
    assert memory.traces == ()

    NullTraceSink().record(trace)

    with pytest.raises(ValueError):
        memory.record("not a trace")  # type: ignore[arg-type]


def test_jsonl_sink_appends_parseable_lines(tmp_path) -> None:
    path = tmp_path / "nested" / "agent-trace.jsonl"
    failure = make_trace(
        ok=False,
        error=ErrorInfo(ErrorCode.TOOL_EXECUTION_FAILED, "除零", {"reason": "x"}),
    )

    with JsonlTraceSink(path) as sink:
        sink.record(make_trace())
        sink.record(failure)

    with JsonlTraceSink(path) as sink:
        sink.record(make_trace(sequence=2))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert [record["status"] for record in records] == [
        "success",
        "failure",
        "success",
    ]
    assert records[1]["error"]["code"] == "tool_execution_failed"
    assert records[2]["sequence"] == 2


def test_sinks_satisfy_protocol() -> None:
    assert isinstance(MemoryTraceSink(), TraceSink)
    assert isinstance(NullTraceSink(), TraceSink)

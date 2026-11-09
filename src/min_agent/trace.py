"""P4：工具调用的结构化 trace。

每条 trace 记录一次工具执行，包含时间、``session_id``、轮次 ID、轮次内序号、
工具名、参数、成功/失败、耗时与结构化错误。Trace 不包含模型隐藏推理、
不包含 API Key，也不读取环境配置。

接收方通过 ``TraceSink`` 协议解耦：CLI 默认写 JSON Lines 文件，测试使用
内存实现；Loop 只负责构造 ``ToolTrace``。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import ErrorInfo


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_int(value: Any, field_name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class ToolTrace:
    """一次工具执行的不可变记录。"""

    timestamp: datetime
    session_id: str
    turn_id: str
    sequence: int
    call_id: str
    tool: str
    arguments: Mapping[str, Any]
    ok: bool
    duration_ms: int
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be a timezone-aware datetime")
        object.__setattr__(
            self, "session_id", _require_text(self.session_id, "session_id")
        )
        object.__setattr__(self, "turn_id", _require_text(self.turn_id, "turn_id"))
        object.__setattr__(
            self, "sequence", _require_int(self.sequence, "sequence", 1)
        )
        object.__setattr__(self, "call_id", _require_text(self.call_id, "call_id"))
        object.__setattr__(self, "tool", _require_text(self.tool, "tool"))
        if not isinstance(self.arguments, Mapping):
            raise ValueError("arguments must be a mapping")
        if any(not isinstance(key, str) for key in self.arguments):
            raise ValueError("argument keys must be strings")
        object.__setattr__(self, "arguments", dict(self.arguments))
        if not isinstance(self.ok, bool):
            raise ValueError("ok must be a bool")
        object.__setattr__(
            self, "duration_ms", _require_int(self.duration_ms, "duration_ms", 0)
        )
        if self.ok:
            if self.error is not None:
                raise ValueError("successful tool trace must not carry an error")
        elif not isinstance(self.error, ErrorInfo):
            raise ValueError("failed tool trace must carry an ErrorInfo")

    @property
    def status(self) -> str:
        return "success" if self.ok else "failure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "call_id": self.call_id,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "ok": self.ok,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@runtime_checkable
class TraceSink(Protocol):
    """trace 接收方；Loop 只依赖 ``record``。"""

    def record(self, trace: ToolTrace) -> None: ...


class NullTraceSink:
    """丢弃 trace 的默认实现，便于测试和不需要日志的调用方。"""

    def record(self, trace: ToolTrace) -> None:
        if not isinstance(trace, ToolTrace):
            raise ValueError("trace must be a ToolTrace")


class MemoryTraceSink:
    """内存 trace 收集器，供测试断言。"""

    def __init__(self) -> None:
        self._traces: list[ToolTrace] = []

    @property
    def traces(self) -> tuple[ToolTrace, ...]:
        return tuple(self._traces)

    def record(self, trace: ToolTrace) -> None:
        if not isinstance(trace, ToolTrace):
            raise ValueError("trace must be a ToolTrace")
        self._traces.append(trace)

    def clear(self) -> None:
        self._traces.clear()


class JsonlTraceSink:
    """追加写入 JSON Lines 文件；每行一条完整 trace 并立即 flush。"""

    def __init__(self, path: str | Path) -> None:
        if isinstance(path, bool) or not isinstance(path, (str, Path)):
            raise ValueError("path must be a string or Path")
        file_path = Path(path)
        if not file_path.name:
            raise ValueError("path must point to a file")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = file_path
        self._file = file_path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def record(self, trace: ToolTrace) -> None:
        if not isinstance(trace, ToolTrace):
            raise ValueError("trace must be a ToolTrace")
        line = json.dumps(
            trace.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> JsonlTraceSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = [
    "JsonlTraceSink",
    "MemoryTraceSink",
    "NullTraceSink",
    "ToolTrace",
    "TraceSink",
]

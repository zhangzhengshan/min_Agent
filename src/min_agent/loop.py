"""P4：核心 Agent Loop、异常处理与工具 trace。

一轮对话的流程：

1. 用当前 Session 历史 + 本轮用户输入 + 已发生的工具调用/结果构造请求；
2. 调用 LLM，把响应严格解析为 ``tool_call`` 或 ``final``；
3. ``final``：完成轮次并写入 Session（必要时触发 P1 的滚动摘要）；
4. ``tool_call``：执行工具，把结果写回当前轮次，再回到第 1 步让模型判断；
5. 达到工具调用上限、malformed 重试耗尽、模型/摘要失败时终止本轮并返回
   结构化错误，不抛出未处理异常。

边界：

- malformed 输出最多重试 ``max_malformed_retries`` 次（默认 1），每次重试在
  请求末尾附一条纠错提示；该提示不写入 Session 历史；
- 未知工具、参数错误和工具内部异常由 ``ToolExecutor`` 转成失败结果反馈模型，
  与成功调用一样计入单轮工具调用上限；
- trace 只记录工具调用，不记录 API Key、完整提示词或模型隐藏推理。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .builtin_tools import create_default_registry
from .errors import (
    AgentError,
    ErrorCode,
    ErrorInfo,
    ModelError,
    ParseError,
    SummarizationError,
)
from .llm import LLMClient, build_turn_request
from .models import Turn
from .parser import parse_decision
from .session import DEFAULT_SYSTEM_PROMPT, Session
from .tools import ToolExecutor, ToolRegistry
from .trace import NullTraceSink, ToolTrace, TraceSink

DEFAULT_MAX_TOOL_CALLS = 5
DEFAULT_MALFORMED_RETRIES = 1


def _require_limit(name: str, value: Any, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class LoopResult:
    """一次 ``AgentLoop.run`` 的结果。

    正常完成时 ``error`` 为 ``None`` 且 ``recorded`` 为 ``True``。
    摘要失败时模型答案已经生成，但轮次未写入 Session，因此 ``error`` 非空、
    ``recorded`` 为 ``False``；调用方应展示错误而不是答案。
    """

    turn: Turn
    error: ErrorInfo | None = None
    recorded: bool = False
    tool_calls: int = 0
    malformed_retries: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.turn, Turn):
            raise ValueError("turn must be a Turn")
        if self.error is not None and not isinstance(self.error, ErrorInfo):
            raise ValueError("error must be an ErrorInfo or None")
        if not isinstance(self.recorded, bool):
            raise ValueError("recorded must be a bool")
        _require_limit("tool_calls", self.tool_calls, 0)
        _require_limit("malformed_retries", self.malformed_retries, 0)

    @property
    def ok(self) -> bool:
        return self.error is None and self.turn.is_completed

    @property
    def answer(self) -> str | None:
        return self.turn.answer if self.ok else None

    @property
    def session_id(self) -> str:
        return self.turn.session_id

    @property
    def error_code(self) -> str | None:
        return self.error.code if self.error is not None else None


class AgentLoop:
    """串起 Session、Context、LLM、工具与 trace 的最小运行时。"""

    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry | None = None,
        *,
        executor: ToolExecutor | None = None,
        trace_sink: TraceSink | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        max_malformed_retries: int = DEFAULT_MALFORMED_RETRIES,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if not callable(getattr(client, "complete", None)):
            raise ValueError("client must expose a callable complete()")
        if executor is not None and not isinstance(executor, ToolExecutor):
            raise ValueError("executor must be a ToolExecutor or None")
        if registry is None:
            registry = executor.registry if executor is not None else (
                create_default_registry()
            )
        if not isinstance(registry, ToolRegistry):
            raise ValueError("registry must be a ToolRegistry or None")
        if executor is None:
            executor = ToolExecutor(registry)
        elif executor.registry is not registry:
            raise ValueError("executor and registry must share the same registry")
        if trace_sink is not None and not callable(
            getattr(trace_sink, "record", None)
        ):
            raise ValueError("trace_sink must expose a callable record()")

        self._client = client
        self._registry = registry
        self._executor = executor
        self._trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self._max_tool_calls = _require_limit(
            "max_tool_calls", max_tool_calls, 1
        )
        self._max_malformed_retries = _require_limit(
            "max_malformed_retries", max_malformed_retries, 0
        )
        self._system_prompt = _require_text(system_prompt, "system_prompt")

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def max_tool_calls(self) -> int:
        return self._max_tool_calls

    @property
    def max_malformed_retries(self) -> int:
        return self._max_malformed_retries

    def run(self, session: Session, user_input: str) -> LoopResult:
        """执行一轮对话；所有已声明失败都转为 ``LoopResult.error``。"""
        if not isinstance(session, Session):
            raise ValueError("session must be a Session")
        _require_text(user_input, "user_input")

        turn = Turn(session_id=session.session_id, user_input=user_input)
        malformed_retries = 0
        notice: str | None = None

        while True:
            request = build_turn_request(
                session,
                turn,
                self._registry,
                system_prompt=self._system_prompt,
                notices=(notice,) if notice is not None else (),
            )
            notice = None

            try:
                raw = self._client.complete(request)
            except ParseError as exc:
                if malformed_retries >= self._max_malformed_retries:
                    return self._fail(
                        turn,
                        exc.to_info(),
                        malformed_retries=malformed_retries,
                    )
                malformed_retries += 1
                notice = self._malformed_notice(exc.to_info())
                continue
            except ModelError as exc:
                return self._fail(
                    turn,
                    exc.to_info(),
                    malformed_retries=malformed_retries,
                )
            except AgentError as exc:
                return self._fail(
                    turn,
                    exc.to_info(),
                    malformed_retries=malformed_retries,
                )
            except Exception as exc:  # noqa: BLE001 - 未知客户端异常统一为本轮失败
                return self._fail(
                    turn,
                    ErrorInfo(
                        ErrorCode.MODEL_REQUEST_FAILED,
                        "模型请求失败",
                        {"cause_type": type(exc).__name__},
                    ),
                    malformed_retries=malformed_retries,
                )

            try:
                decision = parse_decision(raw)
            except ParseError as exc:
                if malformed_retries >= self._max_malformed_retries:
                    return self._fail(
                        turn,
                        exc.to_info(),
                        malformed_retries=malformed_retries,
                    )
                malformed_retries += 1
                notice = self._malformed_notice(exc.to_info())
                continue
            except ValueError as exc:
                info = ErrorInfo(
                    ErrorCode.OUTPUT_PARSE_FAILED,
                    "模型输出无法解析为决策",
                    {"reason": "invalid_decision", "cause_type": type(exc).__name__},
                )
                if malformed_retries >= self._max_malformed_retries:
                    return self._fail(
                        turn,
                        info,
                        malformed_retries=malformed_retries,
                    )
                malformed_retries += 1
                notice = self._malformed_notice(info)
                continue

            if decision.is_final:
                return self._finish(
                    session,
                    turn,
                    decision.answer,
                    malformed_retries=malformed_retries,
                )

            call = decision.tool_call
            if call is None:  # 防御：解析器保证 tool_call 决策必有调用
                return self._fail(
                    turn,
                    ErrorInfo(
                        ErrorCode.OUTPUT_PARSE_FAILED,
                        "tool_call 决策缺少工具调用",
                        {"reason": "missing_tool_call"},
                    ),
                    malformed_retries=malformed_retries,
                )

            if len(turn.tool_exchanges) >= self._max_tool_calls:
                return self._fail(
                    turn,
                    ErrorInfo(
                        ErrorCode.TOOL_LIMIT_EXCEEDED,
                        f"本轮工具调用达到上限（{self._max_tool_calls} 次），已停止",
                        {
                            "max_tool_calls": self._max_tool_calls,
                            "attempted_tool": call.name,
                        },
                    ),
                    malformed_retries=malformed_retries,
                )

            started = time.perf_counter()
            result = self._executor.execute(call, session=session)
            duration_ms = max(
                0, int(round((time.perf_counter() - started) * 1000))
            )
            turn.add_exchange(
                call,
                result,
                thought_summary=decision.thought_summary,
            )

            trace = ToolTrace(
                timestamp=datetime.now(timezone.utc),
                session_id=session.session_id,
                turn_id=turn.turn_id,
                sequence=len(turn.tool_exchanges),
                call_id=call.call_id,
                tool=call.name,
                arguments=call.arguments,
                ok=result.ok,
                duration_ms=duration_ms,
                error=result.error,
            )
            try:
                self._trace_sink.record(trace)
            except Exception as exc:  # noqa: BLE001 - trace 失败不抛出堆栈
                return self._fail(
                    turn,
                    ErrorInfo(
                        ErrorCode.AGENT_ERROR,
                        "工具调用 trace 写入失败",
                        {"cause_type": type(exc).__name__},
                    ),
                    malformed_retries=malformed_retries,
                )

    def _finish(
        self,
        session: Session,
        turn: Turn,
        answer: str | None,
        *,
        malformed_retries: int,
    ) -> LoopResult:
        if answer is None:  # 防御：解析器保证 final 决策必有答案
            return self._fail(
                turn,
                ErrorInfo(
                    ErrorCode.OUTPUT_PARSE_FAILED,
                    "final 决策缺少答案",
                    {"reason": "missing_answer"},
                ),
                malformed_retries=malformed_retries,
            )
        turn.complete(answer)
        try:
            session.record_turn(turn)
        except SummarizationError as exc:
            return LoopResult(
                turn=turn,
                error=exc.to_info(),
                recorded=False,
                tool_calls=len(turn.tool_exchanges),
                malformed_retries=malformed_retries,
            )
        return LoopResult(
            turn=turn,
            recorded=True,
            tool_calls=len(turn.tool_exchanges),
            malformed_retries=malformed_retries,
        )

    def _fail(
        self,
        turn: Turn,
        info: ErrorInfo,
        *,
        malformed_retries: int,
    ) -> LoopResult:
        if turn.is_open:
            turn.fail(info)
        return LoopResult(
            turn=turn,
            error=info,
            recorded=False,
            tool_calls=len(turn.tool_exchanges),
            malformed_retries=malformed_retries,
        )

    def _malformed_notice(self, info: ErrorInfo) -> str:
        reason = info.details.get("reason", "invalid_response")
        return (
            "你上一次的响应无法解析为合法决策"
            f"（原因：{reason}）。不要输出解释或 Markdown，只输出一个符合响应"
            '协议的 JSON 对象，例如 {"kind": "final", "answer": "..."} 或 '
            '{"kind": "tool_call", "tool_call": {"name": "...", "arguments": {}}}。'
        )


__all__ = [
    "AgentLoop",
    "DEFAULT_MALFORMED_RETRIES",
    "DEFAULT_MAX_TOOL_CALLS",
    "LoopResult",
]

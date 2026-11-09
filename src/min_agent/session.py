"""P1：进程内 Session 管理、滚动摘要与工具状态隔离。

设计要点：

- ``SessionManager`` 只做进程内登记与切换，不持久化到磁盘；
- 每个 ``Session`` 独立保存完整轮次、滚动摘要和工具状态，互不共享；
- 只接收已完成的 ``Turn``，避免历史里出现半轮；
- 压缩是事务性的：先让摘要器生成新摘要，成功后一次性替换旧轮次；
  摘要失败时不改动任何历史、摘要或工具状态，并抛出 ``SummarizationError``；
- 摘要器是唯一可替换的外部依赖，P1 测试使用 Fake，P3 再接真实 LLM。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .errors import SessionNotFoundError, SummarizationError
from .models import Turn

DEFAULT_MAX_TURNS = 20
DEFAULT_RECENT_TURNS = 10
DEFAULT_SYSTEM_PROMPT = (
    "你是一个最小可用的智能体。根据对话决定直接回答，或调用可用工具获取信息；"
    "回答保持简洁。"
)
SUMMARY_PREFIX = "以下是本会话较早对话的摘要："
SUMMARY_MESSAGE_NAME = "conversation_summary"


class Summarizer(Protocol):
    """可替换的滚动摘要器。

    实现方负责把 ``previous_summary`` 与 ``turns`` 合并成一份更紧凑的摘要。
    失败时应抛出异常；``Session`` 会把它转成 ``SummarizationError`` 并保留原始历史。
    """

    def summarize(
        self,
        previous_summary: str | None,
        turns: Sequence[Turn],
    ) -> str: ...


def _validate_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_session_id(session_id: Any) -> str:
    return _validate_name(session_id, "session_id")


def _validate_turn_limit(name: str, value: Any, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _validate_limits(max_turns: Any, recent_turns: Any) -> tuple[int, int]:
    max_turns = _validate_turn_limit("max_turns", max_turns, 2)
    recent_turns = _validate_turn_limit("recent_turns", recent_turns, 1)
    if recent_turns >= max_turns:
        raise ValueError("recent_turns must be smaller than max_turns")
    return max_turns, recent_turns


def _validate_summarizer(summarizer: Any) -> Summarizer | None:
    if summarizer is None:
        return None
    if not callable(getattr(summarizer, "summarize", None)):
        raise ValueError("summarizer must expose a callable summarize()")
    return summarizer


class Session:
    """一个独立会话：完整轮次、滚动摘要与工具状态。

    轮次只在完成后写入；历史顺序与对话顺序一致。工具状态按工具名分区，
    P2 的 ``todo`` 等有状态工具通过 ``tool_state_for`` 读写本会话自己的数据。
    """

    def __init__(
        self,
        session_id: str,
        *,
        summarizer: Summarizer | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        recent_turns: int = DEFAULT_RECENT_TURNS,
    ) -> None:
        self._session_id = _validate_session_id(session_id)
        self._max_turns, self._recent_turns = _validate_limits(max_turns, recent_turns)
        self._summarizer = _validate_summarizer(summarizer)
        self._turns: list[Turn] = []
        self._summary: str | None = None
        self._tool_state: dict[str, dict[str, Any]] = {}
        self._recorded_turn_ids: set[str] = set()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def recent_turns(self) -> int:
        return self._recent_turns

    @property
    def turns(self) -> tuple[Turn, ...]:
        """当前保留的完整轮次（只读快照）。"""
        return tuple(self._turns)

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    @property
    def summary(self) -> str | None:
        return self._summary

    @property
    def tool_state(self) -> dict[str, dict[str, Any]]:
        """按工具名分区的可变状态；调用方可直接读取，写入建议用 ``tool_state_for``。"""
        return self._tool_state

    def tool_state_for(self, tool_name: str) -> dict[str, Any]:
        """取得（必要时创建）某个工具在本会话内的状态字典。"""
        name = _validate_name(tool_name, "tool_name")
        return self._tool_state.setdefault(name, {})

    def record_turn(self, turn: Turn) -> None:
        """写入一个已完成轮次，并在超过上限时压缩旧轮次。

        压缩成功后才提交：``summary`` 与 ``turns`` 同时替换，失败时两者都保持原样。
        调用方收到 ``SummarizationError`` 后应终止本轮并向用户返回明确错误。
        """
        if not isinstance(turn, Turn):
            raise ValueError("turn must be a Turn")
        if turn.session_id != self.session_id:
            raise ValueError(
                f"turn belongs to session {turn.session_id!r}, "
                f"not {self.session_id!r}"
            )
        if not turn.is_completed:
            raise ValueError("only completed turns can be recorded")
        if turn.turn_id in self._recorded_turn_ids:
            raise ValueError(f"turn {turn.turn_id!r} was already recorded")

        candidate = [*self._turns, turn]
        if len(candidate) <= self._max_turns:
            self._turns.append(turn)
            self._recorded_turn_ids.add(turn.turn_id)
            return

        new_summary, kept_turns = self._summarize_older(candidate)
        # 摘要器成功返回后才提交，确保失败时不丢历史。
        self._summary = new_summary
        self._turns = kept_turns
        self._recorded_turn_ids.add(turn.turn_id)

    def _summarize_older(self, candidate: list[Turn]) -> tuple[str, list[Turn]]:
        to_compress = candidate[: -self._recent_turns]
        kept = candidate[-self._recent_turns :]
        compressed_ids = [turn.turn_id for turn in to_compress]

        if self._summarizer is None:
            raise SummarizationError(
                "会话超过最大轮次，但没有配置摘要器；历史保持不变",
                {
                    "session_id": self.session_id,
                    "turns": len(candidate),
                    "max_turns": self._max_turns,
                    "recent_turns": self._recent_turns,
                    "compressed_turn_ids": compressed_ids,
                },
            )

        try:
            summary = self._summarizer.summarize(self._summary, tuple(to_compress))
        except Exception as exc:  # noqa: BLE001 - 统一转为结构化运行期错误
            raise SummarizationError(
                "滚动摘要生成失败；历史保持不变",
                {
                    "session_id": self.session_id,
                    "cause_type": type(exc).__name__,
                    "compressed_turn_ids": compressed_ids,
                },
            ) from exc

        if not isinstance(summary, str) or not summary.strip():
            raise SummarizationError(
                "摘要器返回了空摘要；历史保持不变",
                {
                    "session_id": self.session_id,
                    "compressed_turn_ids": compressed_ids,
                },
            )
        return summary.strip(), kept


class SessionManager:
    """进程内会话登记表：创建、获取、切换，重复创建返回原会话。"""

    def __init__(
        self,
        *,
        summarizer: Summarizer | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        recent_turns: int = DEFAULT_RECENT_TURNS,
    ) -> None:
        self._max_turns, self._recent_turns = _validate_limits(max_turns, recent_turns)
        self._summarizer = _validate_summarizer(summarizer)
        self._sessions: dict[str, Session] = {}
        self._current_id: str | None = None

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def recent_turns(self) -> int:
        return self._recent_turns

    @property
    def current(self) -> Session | None:
        """当前选中的会话；尚未创建或切换时为 ``None``。"""
        if self._current_id is None:
            return None
        return self._sessions[self._current_id]

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return isinstance(session_id, str) and session_id.strip() in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self, session_id: str) -> Session:
        """创建会话；同一 ID 已存在时返回现有会话，不重置任何状态。"""
        sid = _validate_session_id(session_id)
        existing = self._sessions.get(sid)
        if existing is not None:
            return existing
        session = Session(
            sid,
            summarizer=self._summarizer,
            max_turns=self._max_turns,
            recent_turns=self._recent_turns,
        )
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session:
        sid = _validate_session_id(session_id)
        try:
            return self._sessions[sid]
        except KeyError:
            raise SessionNotFoundError(
                f"会话 {sid!r} 不存在",
                {"session_id": sid},
            ) from None

    def switch(self, session_id: str) -> Session:
        """切换当前会话并返回它；未知 ID 不会改变当前会话。"""
        session = self.get(session_id)
        self._current_id = session.session_id
        return session

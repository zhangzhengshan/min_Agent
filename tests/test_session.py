"""P1 Session 管理测试：隔离、切换、压缩边界与摘要失败语义。"""

from __future__ import annotations

import pytest

from min_agent.errors import ErrorCode, SessionNotFoundError, SummarizationError
from min_agent.models import Turn
from min_agent.session import (
    DEFAULT_MAX_TURNS,
    DEFAULT_RECENT_TURNS,
    Session,
    SessionManager,
)


class FakeSummarizer:
    """确定性摘要器：记录调用并拼接历史，便于断言传入内容。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, tuple[str, ...]]] = []

    def summarize(self, previous_summary, turns):
        self.calls.append((previous_summary, tuple(turn.user_input for turn in turns)))
        lines = [previous_summary] if previous_summary else []
        for turn in turns:
            lines.append(f"用户：{turn.user_input} / 助手：{turn.answer}")
        return " | ".join(lines)


class FlakySummarizer:
    """第一次失败、第二次成功的摘要器，用于验证失败后可以安全重试。"""

    def __init__(self) -> None:
        self.fail = True
        self.calls = 0

    def summarize(self, previous_summary, turns):
        self.calls += 1
        if self.fail:
            self.fail = False
            raise RuntimeError("summarizer unavailable")
        return "recovered summary"


def make_turn(
    session_id: str = "window-1",
    user_input: str = "hi",
    answer: str = "hello",
) -> Turn:
    turn = Turn(session_id=session_id, user_input=user_input)
    turn.complete(answer)
    return turn


def test_manager_create_is_idempotent_and_keeps_history_and_tool_state() -> None:
    manager = SessionManager(summarizer=FakeSummarizer())
    session = manager.create("window-1")
    assert manager.create("window-1") is session

    session.record_turn(make_turn(user_input="帮我记一下", answer="已记录"))
    session.tool_state_for("todo")["items"] = ["买牛奶"]

    again = manager.create("window-1")

    assert again is session
    assert [turn.user_input for turn in again.turns] == ["帮我记一下"]
    assert again.tool_state_for("todo")["items"] == ["买牛奶"]


def test_duplicate_create_preserves_summary_and_turns_after_compaction() -> None:
    manager = SessionManager(
        summarizer=FakeSummarizer(), max_turns=2, recent_turns=1
    )
    session = manager.create("window-1")
    for index in range(3):
        session.record_turn(
            make_turn(user_input=f"q{index}", answer=f"a{index}")
        )

    summary_before = session.summary
    turn_ids_before = tuple(turn.turn_id for turn in session.turns)
    assert summary_before is not None
    assert len(turn_ids_before) == 1

    again = manager.create("window-1")

    assert again is session
    assert again.summary == summary_before
    assert tuple(turn.turn_id for turn in again.turns) == turn_ids_before


def test_sessions_keep_independent_history_and_tool_state_when_switching() -> None:
    manager = SessionManager(
        summarizer=FakeSummarizer(), max_turns=5, recent_turns=2
    )
    window_1 = manager.create("window-1")
    window_2 = manager.create("window-2")
    assert manager.current is None

    window_1.record_turn(
        make_turn("window-1", "杭州明天天气怎么样", "明天杭州有雨")
    )
    window_1.tool_state_for("todo")["items"] = ["带伞"]
    window_2.record_turn(make_turn("window-2", "帮我写周报", "周报已生成"))
    window_2.tool_state_for("todo")["items"] = ["提交周报"]

    assert manager.switch("window-1") is window_1
    assert [turn.user_input for turn in manager.current.turns] == [
        "杭州明天天气怎么样"
    ]
    assert manager.current.tool_state_for("todo")["items"] == ["带伞"]

    assert manager.switch("window-2") is window_2
    assert [turn.user_input for turn in manager.current.turns] == ["帮我写周报"]
    assert manager.current.tool_state_for("todo")["items"] == ["提交周报"]

    assert manager.switch("window-1") is window_1
    assert [turn.user_input for turn in manager.current.turns] == [
        "杭州明天天气怎么样"
    ]


def test_switch_unknown_session_keeps_current_unchanged() -> None:
    manager = SessionManager()
    window_1 = manager.create("window-1")
    manager.switch("window-1")

    with pytest.raises(SessionNotFoundError) as excinfo:
        manager.switch("missing")

    assert excinfo.value.code is ErrorCode.SESSION_NOT_FOUND
    assert excinfo.value.details == {"session_id": "missing"}
    assert manager.current is window_1


def test_manager_tracks_ids_and_contains() -> None:
    manager = SessionManager()
    assert len(manager) == 0
    assert manager.session_ids == ()
    assert "window-1" not in manager

    manager.create("window-1")
    manager.create("window-2")

    assert len(manager) == 2
    assert manager.session_ids == ("window-1", "window-2")
    assert "window-1" in manager
    assert "  window-2  " in manager


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SessionManager(max_turns=1),
        lambda: SessionManager(max_turns=0),
        lambda: SessionManager(max_turns=True),
        lambda: SessionManager(max_turns=2, recent_turns=0),
        lambda: SessionManager(max_turns=2, recent_turns=2),
        lambda: SessionManager(max_turns=2, recent_turns=3),
        lambda: SessionManager(recent_turns=1.5),
        lambda: Session("window-1", max_turns=1),
    ],
)
def test_invalid_turn_limits_are_rejected(factory) -> None:
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize("session_id", ["", "   ", None, 1])
def test_invalid_session_id_is_rejected(session_id) -> None:
    with pytest.raises(ValueError):
        SessionManager().create(session_id)


def test_default_limits_match_plan() -> None:
    manager = SessionManager()
    assert manager.max_turns == DEFAULT_MAX_TURNS == 20
    assert manager.recent_turns == DEFAULT_RECENT_TURNS == 10


def test_record_turn_requires_completed_turn() -> None:
    session = Session("window-1")

    with pytest.raises(ValueError, match="completed"):
        session.record_turn(Turn(session_id="window-1", user_input="hi"))


def test_record_turn_rejects_foreign_session_and_duplicate_turn() -> None:
    session = Session("window-1")
    foreign = make_turn("window-2", "hi", "hello")
    with pytest.raises(ValueError, match="window-2"):
        session.record_turn(foreign)

    turn = make_turn("window-1", "hi", "hello")
    session.record_turn(turn)
    with pytest.raises(ValueError, match="already recorded"):
        session.record_turn(turn)
    assert session.turn_count == 1


def test_compaction_triggers_only_above_max_and_keeps_recent_turns() -> None:
    summarizer = FakeSummarizer()
    session = Session(
        "window-1", summarizer=summarizer, max_turns=4, recent_turns=2
    )

    for index in range(1, 5):
        session.record_turn(
            make_turn(user_input=f"q{index}", answer=f"a{index}")
        )

    assert summarizer.calls == []
    assert session.summary is None
    assert session.turn_count == 4

    session.record_turn(make_turn(user_input="q5", answer="a5"))

    assert len(summarizer.calls) == 1
    previous_summary, compressed_inputs = summarizer.calls[0]
    assert previous_summary is None
    assert compressed_inputs == ("q1", "q2", "q3")
    assert session.summary is not None
    assert [turn.user_input for turn in session.turns] == ["q4", "q5"]
    assert session.turn_count == 2


def test_repeated_compaction_feeds_previous_summary_forward() -> None:
    summarizer = FakeSummarizer()
    session = Session(
        "window-1", summarizer=summarizer, max_turns=3, recent_turns=1
    )

    for index in range(1, 5):
        session.record_turn(
            make_turn(user_input=f"q{index}", answer=f"a{index}")
        )
    first_summary = session.summary
    assert first_summary is not None
    assert [turn.user_input for turn in session.turns] == ["q4"]

    for index in range(5, 8):
        session.record_turn(
            make_turn(user_input=f"q{index}", answer=f"a{index}")
        )

    assert len(summarizer.calls) == 2
    previous_summary, compressed_inputs = summarizer.calls[1]
    assert previous_summary == first_summary
    assert compressed_inputs == ("q4", "q5", "q6")
    assert session.summary is not None and first_summary in session.summary
    assert [turn.user_input for turn in session.turns] == ["q7"]


def test_summarizer_failure_preserves_history_and_allows_retry() -> None:
    summarizer = FlakySummarizer()
    session = Session(
        "window-1", summarizer=summarizer, max_turns=2, recent_turns=1
    )
    first = make_turn(user_input="q1", answer="a1")
    second = make_turn(user_input="q2", answer="a2")
    session.record_turn(first)
    session.record_turn(second)
    turn_ids_before = [turn.turn_id for turn in session.turns]

    third = make_turn(user_input="q3", answer="a3")
    with pytest.raises(SummarizationError) as excinfo:
        session.record_turn(third)

    assert excinfo.value.code is ErrorCode.SUMMARIZER_FAILED
    assert excinfo.value.details["session_id"] == "window-1"
    assert excinfo.value.details["compressed_turn_ids"] == turn_ids_before
    assert session.summary is None
    assert [turn.turn_id for turn in session.turns] == turn_ids_before

    session.record_turn(third)

    assert session.summary == "recovered summary"
    assert [turn.turn_id for turn in session.turns] == [third.turn_id]


def test_missing_summarizer_is_an_explicit_error_instead_of_silent_loss() -> None:
    session = Session("window-1", max_turns=2, recent_turns=1)
    session.record_turn(make_turn(user_input="q1", answer="a1"))
    session.record_turn(make_turn(user_input="q2", answer="a2"))

    with pytest.raises(SummarizationError) as excinfo:
        session.record_turn(make_turn(user_input="q3", answer="a3"))

    assert excinfo.value.code is ErrorCode.SUMMARIZER_FAILED
    assert "摘要器" in excinfo.value.message
    assert session.turn_count == 2
    assert session.summary is None


def test_blank_summary_is_rejected_without_touching_history() -> None:
    class BlankSummarizer:
        def summarize(self, previous_summary, turns):
            return "   "

    session = Session(
        "window-1", summarizer=BlankSummarizer(), max_turns=2, recent_turns=1
    )
    session.record_turn(make_turn(user_input="q1", answer="a1"))
    session.record_turn(make_turn(user_input="q2", answer="a2"))

    with pytest.raises(SummarizationError, match="空摘要"):
        session.record_turn(make_turn(user_input="q3", answer="a3"))

    assert session.turn_count == 2
    assert session.summary is None


def test_tool_state_namespaces_are_created_on_demand_and_isolated() -> None:
    first = Session("window-1")
    second = Session("window-2")

    first.tool_state_for("todo")["items"] = ["买牛奶"]
    first.tool_state_for("search")["last_query"] = "杭州天气"

    assert second.tool_state == {}
    assert second.tool_state_for("todo") == {}
    assert second.tool_state_for("todo") is second.tool_state_for("todo")
    assert first.tool_state["todo"]["items"] == ["买牛奶"]

    with pytest.raises(ValueError):
        first.tool_state_for("   ")


def test_invalid_summarizer_is_rejected() -> None:
    with pytest.raises(ValueError, match="summarize"):
        SessionManager(summarizer=object())

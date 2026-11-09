"""P4 CLI 测试：会话切换、历史与 Todo 隔离、错误恢复与配置失败。"""

from __future__ import annotations

import json

import pytest

from min_agent import cli as cli_module
from min_agent.builtin_tools import create_default_registry
from min_agent.cli import AgentCLI
from min_agent.llm import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    MockLLMClient,
)
from min_agent.loop import AgentLoop
from min_agent.models import Role
from min_agent.session import SessionManager


def final(answer: str) -> str:
    return json.dumps({"kind": "final", "answer": answer}, ensure_ascii=False)


def tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str | None = None,
) -> str:
    call: dict[str, object] = {"name": name, "arguments": arguments}
    if call_id is not None:
        call["call_id"] = call_id
    return json.dumps(
        {"kind": "tool_call", "tool_call": call},
        ensure_ascii=False,
    )


def make_cli(
    responses: list[str | BaseException],
    **loop_kwargs: object,
) -> tuple[AgentCLI, list[str], SessionManager, MockLLMClient]:
    client = MockLLMClient(responses)
    sessions = SessionManager()
    loop = AgentLoop(
        client,
        create_default_registry(),
        **loop_kwargs,  # type: ignore[arg-type]
    )
    output: list[str] = []
    cli = AgentCLI(loop, sessions, output=output.append)
    return cli, output, sessions, client


def _request_text(client: MockLLMClient, index: int) -> str:
    return "\n".join(message.content for message in client.requests[index].messages)


# ---------------------------------------------------------------------------
# startup and switching
# ---------------------------------------------------------------------------


def test_cli_starts_with_default_session() -> None:
    cli, output, sessions, _ = make_cli([])

    assert cli.current_session_id == "window-1"
    assert sessions.current is not None
    assert sessions.current.session_id == "window-1"
    assert sessions.session_ids == ("window-1",)
    assert "当前会话：window-1" in output[-1]


def test_cli_switch_between_sessions_preserves_each_history() -> None:
    cli, output, sessions, client = make_cli(
        [final("一号回答"), final("二号回答"), final("回一号")]
    )

    assert cli.handle_line("一号问题") is True
    assert output[-1] == "助手：一号回答"
    assert sessions.get("window-1").turn_count == 1

    assert cli.handle_line("/new window-2") is True
    assert sessions.current is not None
    assert sessions.current.session_id == "window-2"
    assert "已创建并切换" in output[-1]

    assert cli.handle_line("二号问题") is True
    assert output[-1] == "助手：二号回答"

    assert cli.handle_line("/use window-1") is True
    assert sessions.current is not None
    assert sessions.current.session_id == "window-1"
    assert "已切换到会话 window-1" in output[-1]

    assert cli.handle_line("继续一号") is True
    assert output[-1] == "助手：回一号"

    window_2_text = _request_text(client, 1)
    assert "一号问题" not in window_2_text
    assert "一号回答" not in window_2_text

    window_1_text = _request_text(client, 2)
    assert "一号问题" in window_1_text
    assert "一号回答" in window_1_text
    assert "二号问题" not in window_1_text

    assert sessions.get("window-1").turn_count == 2
    assert sessions.get("window-2").turn_count == 1


def test_cli_use_unknown_session_does_not_change_current() -> None:
    cli, output, sessions, _ = make_cli([])

    assert cli.handle_line("/use missing") is True

    assert sessions.current is not None
    assert sessions.current.session_id == "window-1"
    assert "不存在" in output[-1]


def test_cli_new_existing_session_keeps_history() -> None:
    cli, output, sessions, _ = make_cli([final("你好")])

    cli.handle_line("打个招呼")
    assert sessions.get("window-1").turn_count == 1

    assert cli.handle_line("/new window-1") is True

    assert "历史未清空" in output[-1]
    assert sessions.get("window-1").turn_count == 1
    assert sessions.session_ids == ("window-1",)


def test_cli_commands_help_sessions_blank_unknown_and_exit() -> None:
    cli, output, _, _ = make_cli([])

    before = len(output)
    assert cli.handle_line("   ") is True
    assert len(output) == before

    assert cli.handle_line("/help") is True
    assert "/use" in output[-1]

    assert cli.handle_line("/bogus") is True
    assert "未知命令" in output[-1]

    cli.handle_line("/new window-2")
    assert cli.handle_line("/sessions") is True
    assert "* window-2" in output[-1]
    assert "window-1" in output[-1]

    assert cli.handle_line("/exit") is False
    assert cli.handle_line("/quit") is False


# ---------------------------------------------------------------------------
# chat errors and isolation
# ---------------------------------------------------------------------------


def test_cli_recovers_after_malformed_turn() -> None:
    cli, output, sessions, client = make_cli(
        ["bad", "still bad", final("恢复")]
    )

    assert cli.handle_line("问题一") is True
    assert "output_parse_failed" in output[-1]
    assert sessions.get("window-1").turn_count == 0

    assert cli.handle_line("问题二") is True
    assert output[-1] == "助手：恢复"
    assert sessions.get("window-1").turn_count == 1
    assert len(client.requests) == 3


def test_cli_todo_is_isolated_between_sessions() -> None:
    cli, output, sessions, client = make_cli(
        [
            tool_call("todo", {"action": "add", "text": "买牛奶"}, call_id="c1"),
            final("已记录"),
            tool_call("todo", {"action": "list"}, call_id="c2"),
            final("没有待办"),
            tool_call("todo", {"action": "list"}, call_id="c3"),
            final("有一条"),
        ]
    )

    cli.handle_line("记一下买牛奶")
    assert output[-1] == "助手：已记录"

    cli.handle_line("/new window-2")
    cli.handle_line("我的待办是什么")
    assert output[-1] == "助手：没有待办"

    cli.handle_line("/use window-1")
    cli.handle_line("看看待办")
    assert output[-1] == "助手：有一条"

    items_1 = sessions.get("window-1").tool_state_for("todo")["items"]
    items_2 = sessions.get("window-2").tool_state_for("todo")["items"]
    assert [item["text"] for item in items_1] == ["买牛奶"]
    assert items_2 == []

    window_2_final = [
        m for m in client.requests[3].messages if m.role is Role.TOOL
    ][-1]
    assert '"items": []' in window_2_final.content
    assert "买牛奶" in _request_text(client, 5)


def test_cli_run_loop_and_exit() -> None:
    cli, output, _, _ = make_cli([final("你好")])
    lines = iter(["你好", "/exit"])

    code = cli.run(read=lambda prompt: next(lines))

    assert code == 0
    assert "助手：你好" in output


def test_cli_run_treats_eof_as_normal_exit() -> None:
    cli, output, _, _ = make_cli([])

    def read(prompt: str) -> str:
        raise EOFError

    assert cli.run(read=read) == 0
    assert output[-1] == ""


def test_cli_run_treats_ctrl_c_during_chat_as_normal_exit() -> None:
    cli, output, _, _ = make_cli([KeyboardInterrupt()])

    assert cli.run(read=lambda prompt: "你好") == 0
    assert output[-1] == ""


# ---------------------------------------------------------------------------
# main() configuration errors
# ---------------------------------------------------------------------------


def test_main_reports_missing_config_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (ENV_BASE_URL, ENV_MODEL, ENV_API_KEY):
        monkeypatch.delenv(name, raising=False)

    code = cli_module.main(["--env", str(tmp_path / "missing.env")])

    assert code == 2
    captured = capsys.readouterr()
    assert "配置错误" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_main_rejects_invalid_tool_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_BASE_URL, "https://api.example.test/v1")
    monkeypatch.setenv(ENV_MODEL, "test-model")
    monkeypatch.setenv(ENV_API_KEY, "sk-test-not-a-real-secret")

    code = cli_module.main(
        [
            "--env",
            str(tmp_path / "missing.env"),
            "--trace",
            str(tmp_path / "trace.jsonl"),
            "--max-tool-calls",
            "0",
        ]
    )

    assert code == 2
    captured = capsys.readouterr()
    assert "配置错误" in captured.err
    assert "Traceback" not in captured.err
    assert "sk-test-not-a-real-secret" not in captured.err

"""P4：单进程 CLI。

用法::

    python -m min_agent

命令：

- ``/use <session_id>``：切换到已有会话；未知 ID 不会改变当前会话；
- ``/new <session_id>``：创建会话并切换；已存在时直接切换且不清空历史；
- ``/sessions``：列出全部会话；
- ``/help``：显示帮助；
- ``/exit``（或 ``/quit``）：退出。

其余输入作为当前会话的一轮对话交给 ``AgentLoop``。所有 Session 只存在于
当前进程内；切换不会清空任何历史、摘要或 Todo 状态。错误只打印结构化信息，
不向用户暴露堆栈或密钥。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .builtin_tools import create_default_registry
from .errors import AgentError, ConfigError, SessionNotFoundError
from .llm import LLMConfig, LLMSummarizer, OpenAICompatibleClient, load_env_file
from .loop import AgentLoop, DEFAULT_MAX_TOOL_CALLS
from .session import (
    DEFAULT_MAX_TURNS,
    DEFAULT_RECENT_TURNS,
    SessionManager,
)
from .trace import JsonlTraceSink, NullTraceSink, TraceSink

DEFAULT_SESSION_ID = "window-1"
DEFAULT_TRACE_PATH = "logs/agent-trace.jsonl"

BANNER = (
    "min_agent CLI：输入消息开始对话，/help 查看命令，/exit 退出。"
)

HELP_TEXT = """命令：
/use <session_id>  切换到已有会话
/new <session_id>  创建会话并切换（已存在则直接切换，历史不清空）
/sessions          列出全部会话
/help              显示本帮助
/exit 或 /quit     退出
其他输入会作为当前会话的一轮对话发送。"""


class AgentCLI:
    """可注入输入输出与依赖的 CLI，便于自动化测试。"""

    def __init__(
        self,
        loop: AgentLoop,
        sessions: SessionManager,
        *,
        session_id: str = DEFAULT_SESSION_ID,
        output: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(loop, AgentLoop):
            raise ValueError("loop must be an AgentLoop")
        if not isinstance(sessions, SessionManager):
            raise ValueError("sessions must be a SessionManager")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if output is not None and not callable(output):
            raise ValueError("output must be callable or None")

        self._loop = loop
        self._sessions = sessions
        self._output = output if output is not None else print

        self._sessions.create(session_id.strip())
        self._sessions.switch(session_id.strip())
        self._say(BANNER)
        self._say(f"当前会话：{self._current_id}")

    @property
    def current_session_id(self) -> str:
        return self._current_id

    @property
    def _current_id(self) -> str:
        session = self._sessions.current
        if session is None:  # 构造时已创建并切换，正常不会发生
            raise RuntimeError("CLI 没有当前会话")
        return session.session_id

    def _say(self, text: str) -> None:
        self._output(text)

    def handle_line(self, line: str) -> bool:
        """处理一行输入；返回 ``False`` 表示退出。"""
        if not isinstance(line, str):
            raise ValueError("line must be a string")
        text = line.strip()
        if not text:
            return True
        if text.startswith("/"):
            return self._handle_command(text)
        self._chat(text)
        return True

    def _handle_command(self, text: str) -> bool:
        name, _, raw_argument = text.partition(" ")
        command = name.lower()
        argument = raw_argument.strip()

        if command in {"/exit", "/quit"}:
            return False
        if command == "/help":
            self._say(HELP_TEXT)
            return True
        if command == "/sessions":
            self._list_sessions()
            return True
        if command == "/new":
            self._new_session(argument)
            return True
        if command == "/use":
            self._use_session(argument)
            return True

        self._say(f"未知命令：{command}。输入 /help 查看可用命令。")
        return True

    def _new_session(self, session_id: str) -> None:
        if not session_id:
            self._say("用法：/new <session_id>")
            return
        existed = session_id in self._sessions
        session = self._sessions.create(session_id)
        self._sessions.switch(session.session_id)
        if existed:
            self._say(
                f"会话 {session.session_id} 已存在，已直接切换（历史未清空）。"
            )
        else:
            self._say(f"已创建并切换到会话 {session.session_id}。")

    def _use_session(self, session_id: str) -> None:
        if not session_id:
            self._say("用法：/use <session_id>")
            return
        try:
            session = self._sessions.switch(session_id)
        except SessionNotFoundError:
            self._say(
                f"会话 {session_id} 不存在。可先用 /new {session_id} 创建。"
            )
            return
        self._say(f"已切换到会话 {session.session_id}。")

    def _list_sessions(self) -> None:
        current = self._current_id
        lines = ["会话："]
        for session_id in self._sessions.session_ids:
            marker = "*" if session_id == current else " "
            lines.append(f"{marker} {session_id}")
        self._say("\n".join(lines))

    def _chat(self, text: str) -> None:
        session = self._sessions.current
        if session is None:  # 防御
            self._say("当前没有可用会话，请先用 /new <session_id> 创建。")
            return
        try:
            result = self._loop.run(session, text)
        except AgentError as exc:
            self._say(f"错误（{exc.code.value}）：{exc.message}")
            return
        except Exception as exc:  # noqa: BLE001 - CLI 不向用户暴露堆栈
            self._say(f"内部错误：{type(exc).__name__}")
            return

        if result.ok:
            self._say(f"助手：{result.answer}")
            return
        if result.error is not None:
            self._say(f"错误（{result.error.code}）：{result.error.message}")
            return
        self._say("本轮未完成。")

    def run(self, read: Callable[[str], str] | None = None) -> int:
        """读取-求值-打印循环；EOF 或 Ctrl+C 正常退出。"""
        reader = read if read is not None else input
        while True:
            try:
                line = reader(f"[{self._current_id}] 你： ")
                if not self.handle_line(line):
                    return 0
            except (EOFError, KeyboardInterrupt):
                self._say("")
                return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="min-agent",
        description="从零实现的最小 Agent CLI（真实 LLM + 三个内置工具）。",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="环境变量文件路径（默认 .env；不存在时忽略）",
    )
    parser.add_argument(
        "--trace",
        default=DEFAULT_TRACE_PATH,
        help=f"工具调用 trace 的 JSONL 路径（默认 {DEFAULT_TRACE_PATH}）",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="不写 trace 文件",
    )
    parser.add_argument(
        "--session",
        default=DEFAULT_SESSION_ID,
        help=f"启动时创建并使用的会话 ID（默认 {DEFAULT_SESSION_ID}）",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        help=f"单轮工具调用上限（默认 {DEFAULT_MAX_TOOL_CALLS}）",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=f"Session 最大完整轮次（默认 {DEFAULT_MAX_TURNS}）",
    )
    parser.add_argument(
        "--recent-turns",
        type=int,
        default=DEFAULT_RECENT_TURNS,
        help=f"压缩后保留的近期完整轮次（默认 {DEFAULT_RECENT_TURNS}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；配置或参数错误返回 2，不打印堆栈。"""
    args = _build_parser().parse_args(argv)

    try:
        env_path = Path(args.env)
        if env_path.is_file():
            load_env_file(env_path)
        config = LLMConfig.from_env()
        client = OpenAICompatibleClient(config)
    except ConfigError as exc:
        print(f"配置错误：{exc.message}", file=sys.stderr)
        print(
            "请设置 MIN_AGENT_LLM_BASE_URL、MIN_AGENT_LLM_MODEL、"
            "MIN_AGENT_LLM_API_KEY 后重试。",
            file=sys.stderr,
        )
        return 2

    sink: TraceSink | None = None
    try:
        summarizer = LLMSummarizer(client)
        sessions = SessionManager(
            summarizer=summarizer,
            max_turns=args.max_turns,
            recent_turns=args.recent_turns,
        )
        sink = _make_trace_sink(args.trace, args.no_trace)
        if not args.no_trace:
            print(f"工具 trace：{args.trace}", file=sys.stderr)
        loop = AgentLoop(
            client,
            create_default_registry(),
            trace_sink=sink,
            max_tool_calls=args.max_tool_calls,
        )
        cli = AgentCLI(loop, sessions, session_id=args.session)
    except (ConfigError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        if sink is not None:
            closure = getattr(sink, "close", None)
            if callable(closure):
                closure()
        return 2
    except OSError as exc:
        print(f"无法写入 trace 文件：{type(exc).__name__}", file=sys.stderr)
        return 2

    try:
        return cli.run()
    finally:
        closure = getattr(sink, "close", None)
        if callable(closure):
            closure()


def _make_trace_sink(path: str, disabled: bool) -> TraceSink:
    if disabled:
        return NullTraceSink()
    return JsonlTraceSink(path)


__all__ = [
    "AgentCLI",
    "BANNER",
    "DEFAULT_SESSION_ID",
    "DEFAULT_TRACE_PATH",
    "HELP_TEXT",
    "main",
]

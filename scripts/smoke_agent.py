"""P5 真实接口最终冒烟：五种基础路径 + 双 Session 场景。

覆盖：

1. 直接回复（不调用工具）；
2. 调用 ``calculator`` 并依据结果回答；
3. 调用 Mock ``search`` 并依据结果回答；
4. 调用 ``todo`` 新增/列出待办；
5. 一个轮次内连续调用 ``search`` + ``calculator``，再根据工具结果总结；
6. ``window-1`` 天气 + 待办、``window-2`` 周报 + 待办，切回后分别追问。

凭据只从环境变量或项目根目录 ``.env`` 读取，不写入代码、结果或 trace。
成功退出码 0；任一用例失败为 1。结果与 trace 写入 ``experiments/``。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from min_agent.builtin_tools import create_default_registry  # noqa: E402
from min_agent.errors import ConfigError  # noqa: E402
from min_agent.llm import (  # noqa: E402
    LLMConfig,
    OpenAICompatibleClient,
    load_env_file,
)
from min_agent.loop import AgentLoop, LoopResult  # noqa: E402
from min_agent.session import Session, SessionManager  # noqa: E402
from min_agent.trace import JsonlTraceSink  # noqa: E402


class RecordingClient:
    """记录真实模型请求，供上下文隔离断言；不记录密钥。"""

    def __init__(self, inner: OpenAICompatibleClient) -> None:
        self._inner = inner
        self.requests: list[tuple[Any, str | None]] = []

    def complete(self, request):
        try:
            raw = self._inner.complete(request)
        except BaseException:
            self.requests.append((request, None))
            raise
        self.requests.append((request, raw))
        return raw


def _turn_record(result: LoopResult) -> dict[str, Any]:
    return {
        "input": result.turn.user_input,
        "ok": result.ok,
        "recorded": result.recorded,
        "answer": result.answer,
        "error": result.error.to_dict() if result.error is not None else None,
        "tools": [
            {
                "name": exchange.tool_call.name,
                "arguments": exchange.tool_call.arguments,
                "ok": exchange.tool_result.ok,
                "output": exchange.tool_result.output,
                "error": (
                    exchange.tool_result.error.to_dict()
                    if exchange.tool_result.error is not None
                    else None
                ),
                "thought_summary": exchange.thought_summary,
            }
            for exchange in result.turn.tool_exchanges
        ],
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _successful_tools(result: LoopResult) -> list[str]:
    return [
        exchange.tool_call.name
        for exchange in result.turn.tool_exchanges
        if exchange.tool_result.ok
    ]


def _request_texts(
    client: RecordingClient,
    start: int,
) -> list[str]:
    texts = []
    for request, _raw in client.requests[start:]:
        texts.append(
            "\n".join(
                message.content
                for message in request.messages
                if message.content
            )
        )
    return texts


def _todo_texts(session: Session) -> list[str]:
    state = session.tool_state_for("todo")
    return [item["text"] for item in state.get("items", [])]


def case_greeting(loop: AgentLoop) -> dict[str, Any]:
    session = Session("smoke-greeting")
    result = loop.run(session, "你好，请用一句话介绍你自己。")
    _require(result.ok, "直接回复失败")
    _require(result.tool_calls == 0, "问候不应调用工具")
    _require(bool(result.answer), "问候没有最终答案")
    return {"session_id": session.session_id, "turns": [_turn_record(result)]}


def case_calculator(loop: AgentLoop) -> dict[str, Any]:
    session = Session("smoke-calculator")
    result = loop.run(
        session,
        "请计算 123*45，必须使用 calculator 工具得到精确结果。",
    )
    _require(result.ok, "calculator 路径失败")
    _require(_successful_tools(result) == ["calculator"], "未成功调用 calculator")
    output = result.turn.tool_exchanges[0].tool_result.output
    _require(output.get("result") == 5535, f"calculator 结果错误：{output!r}")
    _require("5535" in (result.answer or ""), "最终答案没有使用计算结果")
    return {"session_id": session.session_id, "turns": [_turn_record(result)]}


def case_search(loop: AgentLoop) -> dict[str, Any]:
    session = Session("smoke-search")
    result = loop.run(
        session,
        "请使用 search 工具搜索关键词「杭州天气」，并告诉我搜索结果。",
    )
    _require(result.ok, "search 路径失败")
    _require(_successful_tools(result) == ["search"], "未成功调用 search")
    output = result.turn.tool_exchanges[0].tool_result.output
    _require(output.get("source") == "mock", "search 不是 Mock 结果")
    snippets = [item.get("snippet", "") for item in output.get("results", [])]
    _require(
        any("多云" in snippet for snippet in snippets),
        "search 没有命中固定的杭州天气语料",
    )
    _require("多云" in (result.answer or ""), "最终答案没有使用搜索结果")
    return {"session_id": session.session_id, "turns": [_turn_record(result)]}


def case_todo(loop: AgentLoop) -> dict[str, Any]:
    session = Session("smoke-todo")
    add_result = loop.run(
        session,
        "请使用 todo 工具新增一条待办：提交 P5 测试报告。",
    )
    _require(add_result.ok, "todo add 路径失败")
    _require("todo" in _successful_tools(add_result), "未成功调用 todo add")
    _require(
        _todo_texts(session) == ["提交 P5 测试报告"],
        f"待办未正确保存：{_todo_texts(session)!r}",
    )

    list_result = loop.run(
        session,
        "请使用 todo 工具列出我的全部待办。",
    )
    _require(list_result.ok, "todo list 路径失败")
    _require("todo" in _successful_tools(list_result), "未成功调用 todo list")
    output = list_result.turn.tool_exchanges[-1].tool_result.output
    listed = [item["text"] for item in output.get("items", [])]
    _require(listed == ["提交 P5 测试报告"], f"todo list 结果错误：{listed!r}")
    _require("报告" in (list_result.answer or ""), "最终答案没有使用待办结果")
    return {
        "session_id": session.session_id,
        "turns": [_turn_record(add_result), _turn_record(list_result)],
    }


def case_multi_tool_final(loop: AgentLoop) -> dict[str, Any]:
    session = Session("smoke-multi-tool")
    result = loop.run(
        session,
        "先用 search 工具查杭州天气，再用 calculator 计算 18*2，"
        "最后用一句中文同时总结天气和计算结果。",
    )
    _require(result.ok, "多工具路径失败")
    tools = _successful_tools(result)
    _require("search" in tools, "多工具路径未调用 search")
    _require("calculator" in tools, "多工具路径未调用 calculator")
    _require("多云" in (result.answer or ""), "总结没有使用天气结果")
    _require("36" in (result.answer or ""), "总结没有使用计算结果")
    return {"session_id": session.session_id, "turns": [_turn_record(result)]}


def case_dual_session(
    loop: AgentLoop,
    client: RecordingClient,
) -> dict[str, Any]:
    sessions = SessionManager()
    window_1 = sessions.create("window-1")
    window_2 = sessions.create("window-2")

    start_1 = len(client.requests)
    first_1 = loop.run(
        window_1,
        "先用 search 查询「杭州天气」，再用 todo 记一条待办：带伞。",
    )
    _require(first_1.ok, "window-1 首轮失败")
    _require("search" in _successful_tools(first_1), "window-1 未调用 search")
    _require("todo" in _successful_tools(first_1), "window-1 未调用 todo")

    start_2 = len(client.requests)
    first_2 = loop.run(
        window_2,
        "先用 search 查询「周报模板」，再用 todo 记一条待办：写周报。",
    )
    _require(first_2.ok, "window-2 首轮失败")
    _require("search" in _successful_tools(first_2), "window-2 未调用 search")
    _require("todo" in _successful_tools(first_2), "window-2 未调用 todo")

    window_2_text = "\n".join(_request_texts(client, start_2))
    _require("带伞" not in window_2_text, "window-2 混入了 window-1 的待办")
    _require(
        "杭州天气" not in window_2_text,
        "window-2 混入了 window-1 的天气问题",
    )

    start_3 = len(client.requests)
    follow_1 = loop.run(
        window_1,
        "我的待办有哪些？杭州天气怎么样？",
    )
    _require(follow_1.ok, "window-1 追问失败")
    _require("todo" in _successful_tools(follow_1), "window-1 追问未调用 todo")
    _require("带伞" in (follow_1.answer or ""), "window-1 追问未引用自己的待办")
    _require("多云" in (follow_1.answer or ""), "window-1 追问未引用先前天气结果")

    window_1_text = "\n".join(_request_texts(client, start_3))
    _require("带伞" in window_1_text, "window-1 追问上下文缺少自己的待办")
    _require("多云" in window_1_text, "window-1 追问上下文缺少先前工具结果")
    _require("写周报" not in window_1_text, "window-1 混入了 window-2 的待办")

    follow_2 = loop.run(window_2, "我的待办有哪些？")
    _require(follow_2.ok, "window-2 追问失败")
    _require("todo" in _successful_tools(follow_2), "window-2 追问未调用 todo")
    _require("写周报" in (follow_2.answer or ""), "window-2 追问未引用自己的待办")

    _require(_todo_texts(window_1) == ["带伞"], "window-1 待办状态错误")
    _require(_todo_texts(window_2) == ["写周报"], "window-2 待办状态错误")
    return {
        "sessions": {
            "window-1": {
                "turns": [_turn_record(first_1), _turn_record(follow_1)],
                "todo": _todo_texts(window_1),
            },
            "window-2": {
                "turns": [_turn_record(first_2), _turn_record(follow_2)],
                "todo": _todo_texts(window_2),
            },
        }
    }


CASES = (
    ("greeting", case_greeting),
    ("calculator", case_calculator),
    ("search", case_search),
    ("todo", case_todo),
    ("multi_tool_final", case_multi_tool_final),
)


def main() -> int:
    env_file = ROOT / ".env"
    try:
        if env_file.is_file():
            load_env_file(env_file)
        config = LLMConfig.from_env()
    except ConfigError as exc:
        print(f"配置缺失：{exc}")
        print(
            "请设置 MIN_AGENT_LLM_BASE_URL、MIN_AGENT_LLM_MODEL、"
            "MIN_AGENT_LLM_API_KEY 后重试。"
        )
        return 1

    stamp = datetime.now(timezone.utc)
    date_text = stamp.date().isoformat()
    report_path = ROOT / "experiments" / f"{date_text}-p5-final-smoke.json"
    trace_path = ROOT / "experiments" / f"{date_text}-p5-final-smoke-trace.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    client = RecordingClient(OpenAICompatibleClient(config))
    sink = JsonlTraceSink(trace_path)
    loop = AgentLoop(client, create_default_registry(), trace_sink=sink)

    results: list[dict[str, Any]] = []
    try:
        for name, case in CASES:
            record: dict[str, Any] = {"case": name, "ok": False}
            try:
                record.update(case(loop))
                record["ok"] = True
            except Exception as exc:  # noqa: BLE001 - 冒烟记录失败，不中断后续用例
                record["failure"] = f"{type(exc).__name__}: {exc}"
            results.append(record)
            print(f"[{'PASS' if record['ok'] else 'FAIL'}] {name}")

        dual: dict[str, Any] = {"case": "dual_session", "ok": False}
        try:
            dual.update(case_dual_session(loop, client))
            dual["ok"] = True
        except Exception as exc:  # noqa: BLE001
            dual["failure"] = f"{type(exc).__name__}: {exc}"
        results.append(dual)
        print(f"[{'PASS' if dual['ok'] else 'FAIL'}] dual_session")
    finally:
        sink.close()

    passed = all(item["ok"] for item in results)
    report = {
        "timestamp": stamp.isoformat(),
        "base_url": config.base_url,
        "model": config.model,
        "passed": passed,
        "cases": results,
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    trace_text = trace_path.read_text(encoding="utf-8")
    if config.api_key in report_text or config.api_key in trace_text:
        print("检测到密钥出现在冒烟结果中，已终止写入。")
        return 1
    report_path.write_text(report_text, encoding="utf-8")

    print(f"结果已写入 {report_path}")
    print(f"trace 已写入 {trace_path}")
    print("最终冒烟通过" if passed else "最终冒烟未通过")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

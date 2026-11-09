"""P3 真实接口冒烟：问候直接回复 + 明确计算请求选择 calculator。

凭据只从环境变量读取，不写入代码、测试或结果文件。脚本会自动读取项目根目录的
``.env``（已被 Git 忽略）；也可以直接在环境中设置：

    MIN_AGENT_LLM_BASE_URL=https://api.example.com/v1
    MIN_AGENT_LLM_MODEL=<model>
    MIN_AGENT_LLM_API_KEY=<key>
    python scripts/smoke_llm.py

成功退出码为 0，任一用例失败为 1；结果同时写入 experiments/ 便于单独记录。
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
from min_agent.errors import AgentError, ConfigError  # noqa: E402
from min_agent.llm import (  # noqa: E402
    LLMConfig,
    OpenAICompatibleClient,
    build_model_request,
    load_env_file,
)
from min_agent.parser import parse_decision  # noqa: E402
from min_agent.session import Session  # noqa: E402
from min_agent.tools import ToolExecutor  # noqa: E402


def _run_case(
    name: str,
    user_input: str,
    *,
    expect_tool: str | None,
    client: OpenAICompatibleClient,
    registry,
    executor: ToolExecutor,
) -> dict[str, Any]:
    session = Session(f"smoke-{name}")
    result: dict[str, Any] = {"case": name, "input": user_input, "ok": False}

    try:
        request = build_model_request(session, user_input, registry)
        raw = client.complete(request)
        result["raw_response"] = raw
        decision = parse_decision(raw, registry=registry)
    except AgentError as exc:
        result["error"] = exc.to_info().to_dict()
        print(f"[FAIL] {name}: {exc}")
        return result

    result["kind"] = decision.kind.value
    result["thought_summary"] = decision.thought_summary

    if expect_tool is None:
        result["ok"] = decision.is_final
        result["answer"] = decision.answer
    else:
        if decision.is_tool_call and decision.tool_call is not None:
            tool_result = executor.execute(decision.tool_call, session=session)
            result["tool_call"] = decision.tool_call.to_dict()
            result["tool_result"] = tool_result.to_dict()
            result["ok"] = (
                decision.tool_call.name == expect_tool and tool_result.ok
            )
        else:
            result["ok"] = False

    print(f"[{'PASS' if result['ok'] else 'FAIL'}] {name}: {json.dumps(result, ensure_ascii=False)}")
    return result


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

    registry = create_default_registry()
    executor = ToolExecutor(registry)
    client = OpenAICompatibleClient(config)

    results = [
        _run_case(
            "greeting",
            "你好，请用一句话介绍你自己。",
            expect_tool=None,
            client=client,
            registry=registry,
            executor=executor,
        ),
        _run_case(
            "calculator",
            "请计算 123*45，并使用工具得到精确结果。",
            expect_tool="calculator",
            client=client,
            registry=registry,
            executor=executor,
        ),
    ]

    passed = all(item["ok"] for item in results)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": config.base_url,
        "model": config.model,
        "passed": passed,
        "cases": results,
    }
    output = ROOT / "experiments" / (
        f"{datetime.now(timezone.utc).date().isoformat()}-llm-smoke.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结果已写入 {output}")
    print("冒烟通过" if passed else "冒烟未通过")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

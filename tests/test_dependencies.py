"""P0 依赖审计：运行期只允许标准库，测试只允许基础测试库。"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

# 会代替本项目 Runtime 的 Agent 框架（按名称前缀匹配，统一转成 kebab-case）。
FORBIDDEN_PREFIXES = (
    "langchain",
    "langgraph",
    "llama-index",
    "llamaindex",
    "openai-agents",
    "autogen",
    "pyautogen",
    "crewai",
    "semantic-kernel",
    "google-adk",
    "smolagents",
    "openhands",
    "openclaw",
    "pydantic-ai",
    "atomic-agents",
    "agent-framework",
    "agno",
    "haystack",
    "dspy",
)


def load_pyproject() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def dependency_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


def all_declared_requirements(config: dict) -> list[str]:
    project = config.get("project", {})
    requirements = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        requirements.extend(group)
    requirements.extend(config.get("build-system", {}).get("requires", []))
    return requirements


def test_runtime_has_no_third_party_dependencies() -> None:
    assert load_pyproject()["project"]["dependencies"] == []


def test_no_agent_framework_in_any_dependency_group() -> None:
    declared = all_declared_requirements(load_pyproject())
    offending = [
        requirement
        for requirement in declared
        if dependency_name(requirement).startswith(FORBIDDEN_PREFIXES)
    ]

    assert offending == []


def test_dev_dependencies_are_limited_to_test_runner() -> None:
    groups = load_pyproject()["project"].get("optional-dependencies", {})

    assert set(groups) == {"dev"}
    assert {dependency_name(item) for item in groups["dev"]} <= {"pytest"}

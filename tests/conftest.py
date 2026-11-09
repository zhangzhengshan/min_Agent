"""P5：全部自动化测试的离线与无凭据隔离。

- 每个测试开始前清空真实 LLM 配置，确保测试不依赖 API Key；
- 拦截 socket 建连，任何意外的外部网络访问都会让测试立即失败。

需要真实网络的冒烟不放在 ``tests/``，由 ``scripts/smoke_agent.py`` 单独执行。
"""

from __future__ import annotations

import socket

import pytest

LLM_ENV_VARS = (
    "MIN_AGENT_LLM_BASE_URL",
    "MIN_AGENT_LLM_MODEL",
    "MIN_AGENT_LLM_API_KEY",
    "MIN_AGENT_LLM_TIMEOUT",
    "MIN_AGENT_LLM_TEMPERATURE",
    "MIN_AGENT_LLM_MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清空真实凭据并禁止网络连接。"""
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("自动化测试不得访问网络")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

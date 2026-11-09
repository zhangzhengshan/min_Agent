"""P3：LLM 接入、请求构造与摘要器实现。

组成：

- ``LLMRequest``：一次模型请求的内部格式，包含当前 Context 消息和三个工具 Schema；
- ``build_model_request``：把 Session 历史、当前输入、系统指令和工具能力组装成请求；
- ``LLMClient``：最小客户端协议，只有一个 ``complete(request) -> str`` 方法；
- ``OpenAICompatibleClient``：真实模型适配器，使用标准库 HTTP，密钥只从配置注入；
- ``MockLLMClient``：按脚本返回响应，测试全程不访问网络；
- ``LLMSummarizer``：基于同一个 ``LLMClient`` 的滚动摘要器，替换 P1 的 Fake。

安全约定：``LLMConfig.api_key`` 不参与 ``repr``，不写入请求体、日志或错误详情；
错误详情中的密钥文本会被替换为 ``***``。
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .context import build_context, tool_result_content
from .errors import (
    AgentError,
    ConfigError,
    ModelError,
    ParseError,
    SummarizationError,
)
from .models import Message, Role, ToolCall, Turn
from .session import DEFAULT_SYSTEM_PROMPT, Session
from .tools import ToolRegistry

ENV_BASE_URL = "MIN_AGENT_LLM_BASE_URL"
ENV_MODEL = "MIN_AGENT_LLM_MODEL"
ENV_API_KEY = "MIN_AGENT_LLM_API_KEY"
ENV_TIMEOUT = "MIN_AGENT_LLM_TIMEOUT"
ENV_TEMPERATURE = "MIN_AGENT_LLM_TEMPERATURE"
ENV_MAX_TOKENS = "MIN_AGENT_LLM_MAX_TOKENS"

DEFAULT_TIMEOUT = 30.0
DEFAULT_TEMPERATURE = 0.0
_MAX_DETAIL_TEXT = 300
_REDACTED = "***"
_MAX_TOOL_CALLS_PER_RESPONSE = 1

Transport = Callable[[str, dict[str, str], bytes], bytes]

_TOOL_MANIFEST_HEADER = """## 可用工具

以下工具以名称、描述和参数 JSON Schema 提供。需要外部信息或精确计算时调用工具；
不要臆造工具名，也不要编造参数。"""

_RESPONSE_PROTOCOL = """## 响应协议

每次响应只输出一个 JSON 对象，不要输出 Markdown 代码块、前后缀说明或额外文本。
- 直接回答：{"kind": "final", "thought_summary": "简短判断说明", "answer": "给用户的回答"}
- 调用工具：{"kind": "tool_call", "thought_summary": "简短判断说明", "tool_call": {"name": "工具名", "arguments": {}}}
一次响应只能包含一个决策；thought_summary 是可选的简短摘要，不要输出完整推理链。"""


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_DETAIL_TEXT:
        return text
    return text[: _MAX_DETAIL_TEXT - 1] + "…"


def _redact(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, _REDACTED)


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """一次模型请求：消息上下文 + 工具能力信息。"""

    messages: tuple[Message, ...]
    tools: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.messages, (tuple, list)) or not self.messages:
            raise ValueError("messages must be a non-empty sequence of Message")
        if any(not isinstance(message, Message) for message in self.messages):
            raise ValueError("messages must contain only Message objects")
        if not isinstance(self.tools, (tuple, list)):
            raise ValueError("tools must be a sequence of schemas")
        normalized: list[dict[str, Any]] = []
        for schema in self.tools:
            if not isinstance(schema, Mapping):
                raise ValueError("each tool schema must be an object")
            name = schema.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("each tool schema requires a non-empty name")
            if not isinstance(schema.get("description"), str):
                raise ValueError("each tool schema requires a description")
            if not isinstance(schema.get("parameters"), Mapping):
                raise ValueError("each tool schema requires a parameters object")
            normalized.append(copy.deepcopy(dict(schema)))
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(normalized))

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(schema["name"] for schema in self.tools)

    def to_chat_messages(self) -> list[dict[str, Any]]:
        """转换为 chat-completions 风格的消息列表。"""
        rendered: list[dict[str, Any]] = []
        for message in self.messages:
            item: dict[str, Any] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.name is not None:
                item["name"] = message.name
            if message.tool_call_id is not None:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments, ensure_ascii=False
                            ),
                        },
                    }
                    for call in message.tool_calls
                ]
            rendered.append(item)
        return rendered

    def to_payload(
        self,
        *,
        model: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """转换为 OpenAI 兼容的请求体；不包含任何密钥。"""
        model = _require_text(model, "model")
        if isinstance(temperature, bool) or not isinstance(
            temperature, (int, float)
        ):
            raise ValueError("temperature must be a number")
        if not 0 <= float(temperature) <= 2:
            raise ValueError("temperature must be between 0 and 2")
        payload: dict[str, Any] = {
            "model": model,
            "messages": self.to_chat_messages(),
            "temperature": float(temperature),
        }
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
                raise ValueError("max_tokens must be an integer or None")
            if max_tokens < 1:
                raise ValueError("max_tokens must be >= 1")
            payload["max_tokens"] = max_tokens
        if self.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": copy.deepcopy(schema),
                }
                for schema in self.tools
            ]
            payload["tool_choice"] = "auto"
        return payload


def compose_system_prompt(
    base_prompt: str,
    tool_schemas: Sequence[Mapping[str, Any]],
) -> str:
    """在系统指令中附加工具清单与 JSON 响应协议。"""
    base_prompt = _require_text(base_prompt, "base_prompt")
    manifest = json.dumps(
        list(tool_schemas),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n\n".join(
        [
            base_prompt,
            _TOOL_MANIFEST_HEADER,
            f"```json\n{manifest}\n```",
            _RESPONSE_PROTOCOL,
        ]
    )


def build_model_request(
    session: Session,
    user_input: str,
    registry: ToolRegistry,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> LLMRequest:
    """组装一次完整模型请求：当前 Session 的 Context + 三个工具 Schema。"""
    if not isinstance(session, Session):
        raise ValueError("session must be a Session")
    if not isinstance(registry, ToolRegistry):
        raise ValueError("registry must be a ToolRegistry")

    schemas = registry.schemas()
    messages = build_context(
        session,
        user_input,
        system_prompt=compose_system_prompt(system_prompt, schemas),
    )
    return LLMRequest(messages=tuple(messages), tools=tuple(schemas))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """真实模型连接配置；密钥只在此处持有，且不出现在 ``repr`` 中。"""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: float = DEFAULT_TIMEOUT
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        base_url = _require_text(self.base_url, "base_url")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(
                "base_url 必须是 http(s) 地址",
                {"field": "base_url"},
            )
        object.__setattr__(self, "base_url", base_url.rstrip("/"))
        object.__setattr__(self, "model", _require_text(self.model, "model"))
        object.__setattr__(self, "api_key", _require_text(self.api_key, "api_key"))
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or self.timeout <= 0
        ):
            raise ConfigError("timeout 必须是正数", {"field": "timeout"})
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0 <= float(self.temperature) <= 2
        ):
            raise ConfigError(
                "temperature 必须在 0 到 2 之间",
                {"field": "temperature"},
            )
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
        ):
            raise ConfigError(
                "max_tokens 必须是正整数或 None",
                {"field": "max_tokens"},
            )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        """从环境变量读取连接配置；缺失时只报告变量名，不涉及密钥内容。"""
        values = os.environ if env is None else env
        required = {
            ENV_BASE_URL: "base_url",
            ENV_MODEL: "model",
            ENV_API_KEY: "api_key",
        }
        missing = [name for name in required if not str(values.get(name, "")).strip()]
        if missing:
            raise ConfigError(
                "缺少 LLM 配置环境变量",
                {"missing": missing},
            )

        timeout = _parse_number(values.get(ENV_TIMEOUT), DEFAULT_TIMEOUT, ENV_TIMEOUT)
        temperature = _parse_number(
            values.get(ENV_TEMPERATURE), DEFAULT_TEMPERATURE, ENV_TEMPERATURE
        )
        max_tokens = _parse_optional_int(values.get(ENV_MAX_TOKENS), ENV_MAX_TOKENS)

        return cls(
            base_url=str(values[ENV_BASE_URL]).strip(),
            model=str(values[ENV_MODEL]).strip(),
            api_key=str(values[ENV_API_KEY]).strip(),
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def _parse_number(raw: str | None, default: float, name: str) -> float:
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ConfigError(
            f"{name} 必须是数字",
            {"field": name},
        ) from None


def _parse_optional_int(raw: str | None, name: str) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ConfigError(
            f"{name} 必须是整数",
            {"field": name},
        ) from None


_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(
    path: str | os.PathLike[str],
    env: MutableMapping[str, str] | None = None,
    *,
    override: bool = False,
) -> dict[str, str]:
    """把 ``KEY=VALUE`` 配置文件读入环境映射。

    仅支持最小语法：空行、``#`` 注释、可选的 ``export`` 前缀和成对引号；
    错误只报告文件路径与行号，不包含变量值，避免密钥进入错误信息。
    """
    target: MutableMapping[str, str] = os.environ if env is None else env
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(
            "无法读取环境配置文件",
            {"path": str(file_path), "cause_type": type(exc).__name__},
        ) from exc

    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ConfigError(
                "环境配置文件格式错误",
                {"path": str(file_path), "line": line_number},
            )
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_PATTERN.fullmatch(key):
            raise ConfigError(
                "环境配置文件的变量名不合法",
                {"path": str(file_path), "line": line_number},
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        loaded[key] = value
        if override or key not in target:
            target[key] = value
    return loaded


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMClient(Protocol):
    """最小 LLM 客户端协议：输入请求，返回模型原始响应文本。"""

    def complete(self, request: LLMRequest) -> str: ...


class MockLLMClient:
    """按脚本顺序返回响应的测试客户端；可脚本化异常。"""

    def __init__(
        self,
        responses: Sequence[str | BaseException],
        *,
        name: str = "mock-llm",
    ) -> None:
        if isinstance(responses, (str, bytes)) or not isinstance(
            responses, Sequence
        ):
            raise ValueError("responses must be a sequence of strings or exceptions")
        self._name = _require_text(name, "name")
        self._responses: list[str | BaseException] = list(responses)
        self._requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def requests(self) -> tuple[LLMRequest, ...]:
        return tuple(self._requests)

    @property
    def remaining(self) -> int:
        return len(self._responses)

    def complete(self, request: LLMRequest) -> str:
        if not isinstance(request, LLMRequest):
            raise ValueError("request must be an LLMRequest")
        self._requests.append(request)
        if not self._responses:
            raise ModelError(
                "Mock LLM 脚本已耗尽",
                {"client": self._name, "calls": len(self._requests)},
            )
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class OpenAICompatibleClient:
    """OpenAI 兼容 chat-completions 适配器。

    - 地址、模型名、密钥全部来自 ``LLMConfig``，不在代码或测试中写死；
    - 工具通过请求体的 ``tools`` 字段提供，同时系统提示中也有同一份 Schema；
    - 若模型使用原生 function calling，会把首个工具调用归一化为 JSON 文本，
      交给 P3 解析器；一次返回多个工具调用视为非法响应。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Transport | None = None,
    ) -> None:
        if not isinstance(config, LLMConfig):
            raise ValueError("config must be an LLMConfig")
        if transport is not None and not callable(transport):
            raise ValueError("transport must be callable or None")
        self._config = config
        self._transport = transport or self._http_transport

    @property
    def config(self) -> LLMConfig:
        return self._config

    def complete(self, request: LLMRequest) -> str:
        if not isinstance(request, LLMRequest):
            raise ValueError("request must be an LLMRequest")

        payload = request.to_payload(
            model=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }
        url = self._config.endpoint
        safe_url = _redact(url, self._config.api_key)

        try:
            raw = self._transport(url, headers, body)
        except HTTPError as exc:
            raise ModelError(
                "模型接口返回 HTTP 错误",
                {
                    "status": exc.code,
                    "url": safe_url,
                    "detail": self._http_error_detail(exc),
                },
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError(
                "模型接口请求失败",
                {
                    "url": safe_url,
                    "cause_type": type(exc).__name__,
                },
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 传输层异常统一为模型错误
            raise ModelError(
                "模型接口请求失败",
                {
                    "url": safe_url,
                    "cause_type": type(exc).__name__,
                },
            ) from exc

        try:
            return self._extract_response(raw)
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - 响应结构异常统一为模型错误
            raise ModelError(
                "模型响应无法读取",
                {"cause_type": type(exc).__name__},
            ) from exc

    def _http_transport(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> bytes:
        request = Request(url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self._config.timeout) as response:
            return response.read()

    def _http_error_detail(self, exc: HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 尽力读取错误体，失败不影响错误上报
            body = ""
        return _truncate(_redact(body, self._config.api_key))

    def _extract_response(self, raw: Any) -> str:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        elif isinstance(raw, str):
            text = raw
        else:
            raise ModelError(
                "模型接口返回了不支持的内容类型",
                {"got": type(raw).__name__},
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelError(
                "模型接口返回的不是合法 JSON",
                {
                    "line": exc.lineno,
                    "column": exc.colno,
                    "response": _truncate(_redact(text, self._config.api_key)),
                },
            ) from exc

        if not isinstance(data, dict):
            raise ModelError("模型接口响应必须是 JSON 对象", {})

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelError("模型接口响应缺少 choices", {})
        first = choices[0]
        if not isinstance(first, dict):
            raise ModelError("模型接口响应中的 choice 格式错误", {})
        message = first.get("message")
        if not isinstance(message, dict):
            raise ModelError("模型接口响应缺少 message", {})

        tool_calls = message.get("tool_calls")
        if tool_calls:
            return self._normalize_tool_calls(tool_calls, message.get("content"))

        content = message.get("content")
        if content is None:
            return ""
        if not isinstance(content, str):
            raise ModelError(
                "模型响应的 content 不是字符串",
                {"got": type(content).__name__},
            )
        return content

    def _normalize_tool_calls(self, tool_calls: Any, content: Any) -> str:
        if not isinstance(tool_calls, list):
            raise ParseError(
                "模型返回的 tool_calls 格式错误",
                {"reason": "invalid_tool_calls"},
            )
        if len(tool_calls) != _MAX_TOOL_CALLS_PER_RESPONSE:
            raise ParseError(
                "一次响应只能包含一个工具调用",
                {
                    "reason": "multiple_tool_calls",
                    "count": len(tool_calls),
                },
            )

        tool_call = tool_calls[0]
        if not isinstance(tool_call, dict):
            raise ParseError(
                "模型返回的 tool_calls 格式错误",
                {"reason": "invalid_tool_calls"},
            )
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise ParseError(
                "模型返回的工具调用缺少 function",
                {"reason": "invalid_tool_calls"},
            )
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ParseError(
                "模型返回的工具调用缺少名称",
                {"reason": "invalid_tool_calls"},
            )

        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ParseError(
                    "模型返回的工具参数不是合法 JSON",
                    {
                        "reason": "invalid_json",
                        "field": "tool_call.arguments",
                        "line": exc.lineno,
                        "column": exc.colno,
                    },
                ) from exc
        if not isinstance(arguments, dict):
            raise ParseError(
                "模型返回的工具参数必须是 JSON 对象",
                {"reason": "invalid_field", "field": "tool_call.arguments"},
            )

        decision: dict[str, Any] = {
            "kind": "tool_call",
            "tool_call": {
                "name": name.strip(),
                "arguments": arguments,
            },
        }
        call_id = tool_call.get("id")
        if isinstance(call_id, str) and call_id.strip():
            decision["tool_call"]["call_id"] = call_id.strip()
        if isinstance(content, str) and content.strip():
            decision["thought_summary"] = content.strip()
        return json.dumps(decision, ensure_ascii=False)


# ---------------------------------------------------------------------------
# summarizer
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要器。把较早的对话压缩成简洁、准确的中文摘要，"
    "保留用户目标、关键事实、工具调用结果和未完成事项；"
    "不要编造信息，只输出摘要正文，不要输出 JSON 或额外说明。"
)


def _format_summarizer_input(
    previous_summary: str | None,
    turns: Sequence[Turn],
) -> str:
    lines = [
        "已有摘要：",
        previous_summary if previous_summary else "（无）",
        "",
        "需要合并进摘要的新轮次：",
    ]
    for index, turn in enumerate(turns, start=1):
        lines.append(f"轮次 {index}")
        lines.append(f"用户：{turn.user_input}")
        for exchange in turn.tool_exchanges:
            call = exchange.tool_call
            lines.append(
                f"工具调用：{call.name} "
                f"参数={json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
            )
            lines.append(f"工具结果：{tool_result_content(exchange.tool_result)}")
        lines.append(f"助手：{turn.answer if turn.answer is not None else ''}")
        lines.append("")
    return "\n".join(lines).strip()


class LLMSummarizer:
    """基于同一个 ``LLMClient`` 的滚动摘要器。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        system_prompt: str = SUMMARY_SYSTEM_PROMPT,
    ) -> None:
        if not callable(getattr(client, "complete", None)):
            raise ValueError("client must expose a callable complete()")
        self._client = client
        self._system_prompt = _require_text(system_prompt, "system_prompt")

    def summarize(
        self,
        previous_summary: str | None,
        turns: Sequence[Turn],
    ) -> str:
        if previous_summary is not None and not isinstance(previous_summary, str):
            raise ValueError("previous_summary must be a string or None")
        if isinstance(turns, (str, bytes)) or not isinstance(turns, Sequence):
            raise ValueError("turns must be a sequence of Turn")
        if not turns:
            raise ValueError("turns must not be empty")
        if any(not isinstance(turn, Turn) for turn in turns):
            raise ValueError("turns must contain only Turn objects")
        if any(not turn.is_completed for turn in turns):
            raise ValueError("only completed turns can be summarized")

        request = LLMRequest(
            messages=(
                Message(role=Role.SYSTEM, content=self._system_prompt),
                Message(
                    role=Role.USER,
                    content=_format_summarizer_input(previous_summary, turns),
                ),
            ),
            tools=(),
        )
        try:
            summary = self._client.complete(request)
        except SummarizationError:
            raise
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - Session 会统一包装
            raise SummarizationError(
                "摘要模型调用失败",
                {"cause_type": type(exc).__name__},
            ) from exc

        if not isinstance(summary, str) or not summary.strip():
            raise SummarizationError(
                "摘要模型返回空内容",
                {"turns": len(turns)},
            )
        return summary.strip()


__all__ = [
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MAX_TOKENS",
    "ENV_MODEL",
    "ENV_TEMPERATURE",
    "ENV_TIMEOUT",
    "LLMClient",
    "LLMConfig",
    "LLMRequest",
    "LLMSummarizer",
    "MockLLMClient",
    "OpenAICompatibleClient",
    "SUMMARY_SYSTEM_PROMPT",
    "build_model_request",
    "compose_system_prompt",
    "load_env_file",
]

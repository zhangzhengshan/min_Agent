"""P3 LLM 接入测试：请求构造、Mock/真实适配器、配置注入与摘要器接入。"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from min_agent.builtin_tools import create_default_registry
from min_agent.context import SUMMARY_MESSAGE_NAME
from min_agent.errors import (
    ConfigError,
    ErrorCode,
    ModelError,
    ParseError,
    SummarizationError,
)
from min_agent.llm import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_TEMPERATURE,
    ENV_TIMEOUT,
    LLMClient,
    LLMConfig,
    LLMRequest,
    LLMSummarizer,
    MockLLMClient,
    OpenAICompatibleClient,
    build_model_request,
    load_env_file,
)
from min_agent.models import Message, Role, ToolCall, ToolResult, Turn
from min_agent.parser import parse_decision
from min_agent.session import Session

REGISTRY = create_default_registry()
FAKE_KEY = "sk-test-not-a-real-secret"
BASE_URL = "https://api.example.test/v1"


class FixedSummarizer:
    def summarize(self, previous_summary, turns):
        return "较早对话：" + "、".join(turn.user_input for turn in turns)


def make_turn(
    session_id: str = "window-1",
    user_input: str = "hi",
    answer: str = "hello",
    exchanges: list[tuple[ToolCall, ToolResult]] | None = None,
) -> Turn:
    turn = Turn(session_id=session_id, user_input=user_input)
    for call, result in exchanges or []:
        turn.add_exchange(call, result)
    turn.complete(answer)
    return turn


def make_config(**overrides) -> LLMConfig:
    values = {
        "base_url": BASE_URL,
        "model": "test-model",
        "api_key": FAKE_KEY,
    }
    values.update(overrides)
    return LLMConfig(**values)


class FakeTransport:
    def __init__(self, response: bytes | BaseException) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> bytes:
        self.calls.append((url, headers, body))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def chat_response(message: dict) -> bytes:
    return json.dumps({"choices": [{"message": message}]}).encode("utf-8")


# ---------------------------------------------------------------------------
# request construction
# ---------------------------------------------------------------------------


def test_request_includes_current_session_context_and_three_tool_schemas() -> None:
    window_1 = Session("window-1")
    window_2 = Session("window-2")
    window_1.record_turn(make_turn("window-1", "我住在杭州", "记住了"))
    window_2.record_turn(make_turn("window-2", "我在写周报", "好的"))

    request = build_model_request(window_1, "我住哪", REGISTRY)

    assert request.tool_names == ("calculator", "search", "todo")
    assert [tool["name"] for tool in request.tools] == [
        "calculator",
        "search",
        "todo",
    ]
    assert request.messages[0].role is Role.SYSTEM
    system_text = request.messages[0].content
    assert "calculator" in system_text
    assert "search" in system_text
    assert "todo" in system_text
    assert "参数 JSON Schema" in system_text
    assert '"parameters"' in system_text
    assert '{"kind": "final"' in system_text

    user_texts = [
        message.content for message in request.messages if message.role is Role.USER
    ]
    assert user_texts == ["我住在杭州", "我住哪"]
    assert "我在写周报" not in "\n".join(
        message.content for message in request.messages
    )


def test_request_includes_rolling_summary_of_current_session() -> None:
    session = Session(
        "window-1", summarizer=FixedSummarizer(), max_turns=2, recent_turns=1
    )
    for index in range(1, 4):
        session.record_turn(make_turn(user_input=f"q{index}", answer=f"a{index}"))

    request = build_model_request(session, "继续", REGISTRY)

    summary_message = request.messages[1]
    assert summary_message.role is Role.SYSTEM
    assert summary_message.name == SUMMARY_MESSAGE_NAME
    assert session.summary is not None
    assert session.summary in summary_message.content


def test_payload_contains_tools_history_and_no_api_key() -> None:
    call = ToolCall(
        call_id="call-1", name="calculator", arguments={"expression": "1+1"}
    )
    result = ToolResult.success("call-1", "calculator", output=2)
    session = Session("window-1")
    session.record_turn(
        make_turn(
            user_input="1+1 等于几",
            answer="等于 2",
            exchanges=[(call, result)],
        )
    )
    request = build_model_request(session, "再算一次", REGISTRY)

    payload = request.to_payload(model="test-model")

    assert payload["model"] == "test-model"
    assert payload["temperature"] == 0.0
    assert payload["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "calculator",
        "search",
        "todo",
    ]
    roles = [message["role"] for message in payload["messages"]]
    assert roles == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assistant_call = payload["messages"][2]
    assert assistant_call["tool_calls"][0]["id"] == "call-1"
    assert json.loads(
        assistant_call["tool_calls"][0]["function"]["arguments"]
    ) == {"expression": "1+1"}
    assert payload["messages"][3]["tool_call_id"] == "call-1"
    assert payload["messages"][3]["role"] == "tool"
    assert FAKE_KEY not in json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# mock client
# ---------------------------------------------------------------------------


def test_mock_client_returns_scripted_responses_and_records_requests() -> None:
    request = LLMRequest(messages=(Message(role=Role.USER, content="hi"),))
    client = MockLLMClient(['{"kind":"final","answer":"one"}', "two"])

    assert isinstance(client, LLMClient)
    assert client.complete(request) == '{"kind":"final","answer":"one"}'
    assert client.complete(request) == "two"
    assert client.remaining == 0
    assert client.requests == (request, request)


def test_mock_client_exhaustion_raises_model_error() -> None:
    client = MockLLMClient([])
    request = LLMRequest(messages=(Message(role=Role.USER, content="hi"),))

    with pytest.raises(ModelError) as info:
        client.complete(request)

    assert info.value.code is ErrorCode.MODEL_REQUEST_FAILED


def test_mock_client_can_script_an_exception() -> None:
    client = MockLLMClient([RuntimeError("boom")])
    request = LLMRequest(messages=(Message(role=Role.USER, content="hi"),))

    with pytest.raises(RuntimeError, match="boom"):
        client.complete(request)


@pytest.mark.parametrize(
    ("response", "expected_kind", "expected_value"),
    [
        (
            '{"kind":"final","thought_summary":"直接回答","answer":"你好"}',
            "final",
            "你好",
        ),
        (
            '{"kind":"tool_call","thought_summary":"需要计算",'
            '"tool_call":{"name":"calculator","arguments":{"expression":"6*7"}}}',
            "tool_call",
            "calculator",
        ),
    ],
)
def test_mock_response_parses_into_expected_decision(
    response: str,
    expected_kind: str,
    expected_value: str,
) -> None:
    request = build_model_request(Session("window-1"), "你好", REGISTRY)
    client = MockLLMClient([response])

    decision = parse_decision(client.complete(request), registry=REGISTRY)

    assert decision.kind.value == expected_kind
    assert decision.thought_summary is not None
    if expected_kind == "final":
        assert decision.answer == expected_value
    else:
        assert decision.tool_call is not None
        assert decision.tool_call.name == expected_value
        assert decision.tool_call.arguments == {"expression": "6*7"}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_from_env_reads_all_values() -> None:
    config = LLMConfig.from_env(
        {
            ENV_BASE_URL: BASE_URL,
            ENV_MODEL: "env-model",
            ENV_API_KEY: FAKE_KEY,
            ENV_TIMEOUT: "12.5",
            ENV_TEMPERATURE: "0.2",
            ENV_MAX_TOKENS: "512",
        }
    )

    assert config.base_url == BASE_URL
    assert config.model == "env-model"
    assert config.api_key == FAKE_KEY
    assert config.timeout == 12.5
    assert config.temperature == 0.2
    assert config.max_tokens == 512
    assert config.endpoint == f"{BASE_URL}/chat/completions"
    assert FAKE_KEY not in repr(config)


def test_config_from_env_reports_missing_names_without_values() -> None:
    with pytest.raises(ConfigError) as info:
        LLMConfig.from_env({})

    assert info.value.details["missing"] == [
        ENV_BASE_URL,
        ENV_MODEL,
        ENV_API_KEY,
    ]
    assert FAKE_KEY not in str(info.value)


def test_config_rejects_invalid_url_and_numbers() -> None:
    with pytest.raises(ConfigError):
        make_config(base_url="ftp://example.test")
    with pytest.raises(ConfigError):
        LLMConfig.from_env(
            {
                ENV_BASE_URL: BASE_URL,
                ENV_MODEL: "m",
                ENV_API_KEY: FAKE_KEY,
                ENV_TIMEOUT: "abc",
            }
        )
    with pytest.raises(ConfigError):
        LLMConfig.from_env(
            {
                ENV_BASE_URL: BASE_URL,
                ENV_MODEL: "m",
                ENV_API_KEY: FAKE_KEY,
                ENV_MAX_TOKENS: "1.5",
            }
        )


def test_config_accepts_full_chat_completions_endpoint() -> None:
    config = make_config(base_url=f"{BASE_URL}/chat/completions")

    assert config.endpoint == f"{BASE_URL}/chat/completions"


def test_load_env_file_reads_minimal_syntax_and_respects_existing_values(
    tmp_path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nexport FOO='bar'\nBAZ=\"qux\"\nEMPTY=\nFOO=ignored\n",
        encoding="utf-8",
    )
    env = {"FOO": "existing"}

    loaded = load_env_file(path, env)

    assert loaded == {"FOO": "ignored", "BAZ": "qux", "EMPTY": ""}
    assert env["FOO"] == "existing"
    assert env["BAZ"] == "qux"

    load_env_file(path, env, override=True)
    assert env["FOO"] == "ignored"


def test_load_env_file_reports_line_without_leaking_values(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text("GOOD=ok\nNOT A LINE WITH sk-secret\n", encoding="utf-8")

    with pytest.raises(ConfigError) as info:
        load_env_file(path, {})

    assert info.value.details["line"] == 2
    assert "sk-secret" not in str(info.value)

    path.write_text("1BAD=value\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_env_file(path, {})
    assert info.value.details["line"] == 1


def test_load_env_file_missing_file_is_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError) as info:
        load_env_file(tmp_path / "missing.env", {})

    assert info.value.code is ErrorCode.CONFIG_INVALID


# ---------------------------------------------------------------------------
# OpenAI compatible adapter
# ---------------------------------------------------------------------------


def test_real_adapter_sends_context_tools_and_returns_content() -> None:
    session = Session("window-1")
    request = build_model_request(session, "你好", REGISTRY)
    content = '{"kind": "final", "answer": "你好"}'
    transport = FakeTransport(chat_response({"content": content}))
    client = OpenAICompatibleClient(make_config(), transport=transport)

    raw = client.complete(request)

    assert raw == content
    decision = parse_decision(raw, registry=REGISTRY)
    assert decision.is_final
    assert decision.answer == "你好"

    url, headers, body = transport.calls[0]
    assert url == f"{BASE_URL}/chat/completions"
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY.encode() not in body
    payload = json.loads(body)
    assert payload["model"] == "test-model"
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "calculator",
        "search",
        "todo",
    ]
    assert payload["messages"][-1]["content"] == "你好"


def test_real_adapter_normalizes_native_tool_call() -> None:
    request = build_model_request(Session("window-1"), "算一下 2+3", REGISTRY)
    transport = FakeTransport(
        chat_response(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-native",
                        "type": "function",
                        "function": {
                            "name": "calculator",
                            "arguments": '{"expression": "2+3"}',
                        },
                    }
                ],
            }
        )
    )
    client = OpenAICompatibleClient(make_config(), transport=transport)

    raw = client.complete(request)
    decision = parse_decision(raw, registry=REGISTRY)

    assert decision.is_tool_call
    assert decision.tool_call is not None
    assert decision.tool_call.call_id == "call-native"
    assert decision.tool_call.name == "calculator"
    assert decision.tool_call.arguments == {"expression": "2+3"}


def test_real_adapter_rejects_multiple_native_tool_calls() -> None:
    request = build_model_request(Session("window-1"), "算一下", REGISTRY)
    transport = FakeTransport(
        chat_response(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "calculator", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "function": {"name": "search", "arguments": "{}"},
                    },
                ],
            }
        )
    )
    client = OpenAICompatibleClient(make_config(), transport=transport)

    with pytest.raises(ParseError) as info:
        client.complete(request)

    assert info.value.details["reason"] == "multiple_tool_calls"


def test_real_adapter_handles_empty_content_as_empty_response() -> None:
    request = build_model_request(Session("window-1"), "hi", REGISTRY)
    client = OpenAICompatibleClient(
        make_config(), transport=FakeTransport(chat_response({"content": None}))
    )

    assert client.complete(request) == ""


def test_real_adapter_wraps_transport_and_response_errors() -> None:
    request = build_model_request(Session("window-1"), "hi", REGISTRY)
    client = OpenAICompatibleClient(
        make_config(), transport=FakeTransport(b"not json")
    )
    with pytest.raises(ModelError):
        client.complete(request)

    client = OpenAICompatibleClient(
        make_config(), transport=FakeTransport(URLError("offline"))
    )
    with pytest.raises(ModelError) as info:
        client.complete(request)
    assert info.value.details["cause_type"] == "URLError"

    client = OpenAICompatibleClient(
        make_config(), transport=FakeTransport(b'{"no_choices": true}')
    )
    with pytest.raises(ModelError):
        client.complete(request)


def test_http_error_detail_is_redacted() -> None:
    request = build_model_request(Session("window-1"), "hi", REGISTRY)
    error = HTTPError(
        f"{BASE_URL}/chat/completions",
        401,
        "Unauthorized",
        {"Content-Type": "application/json"},
        io.BytesIO(f'{{"error": "bad key {FAKE_KEY}"}}'.encode("utf-8")),
    )
    client = OpenAICompatibleClient(
        make_config(), transport=FakeTransport(error)
    )

    with pytest.raises(ModelError) as info:
        client.complete(request)

    assert info.value.details["status"] == 401
    assert FAKE_KEY not in json.dumps(
        info.value.to_info().to_dict(), ensure_ascii=False
    )
    assert "***" in info.value.details["detail"]


# ---------------------------------------------------------------------------
# summarizer
# ---------------------------------------------------------------------------


def test_llm_summarizer_uses_same_client_and_includes_tool_results() -> None:
    call = ToolCall(
        call_id="call-1", name="calculator", arguments={"expression": "1+1"}
    )
    result = ToolResult.success("call-1", "calculator", output=2)
    turn = make_turn(
        user_input="帮我算 1+1",
        answer="结果是 2",
        exchanges=[(call, result)],
    )
    client = MockLLMClient(["旧摘要 + 新内容"])
    summarizer = LLMSummarizer(client)

    summary = summarizer.summarize("旧摘要", [turn])

    assert summary == "旧摘要 + 新内容"
    request = client.requests[0]
    assert request.tools == ()
    assert request.messages[0].role is Role.SYSTEM
    text = request.messages[1].content
    assert "旧摘要" in text
    assert "帮我算 1+1" in text
    assert "calculator" in text
    assert '"ok": true' in text
    assert "结果是 2" in text


def test_llm_summarizer_rejects_empty_model_output() -> None:
    client = MockLLMClient(["   "])
    summarizer = LLMSummarizer(client)

    with pytest.raises(SummarizationError):
        summarizer.summarize(None, [make_turn()])


def test_llm_summarizer_rejects_open_or_empty_turns() -> None:
    summarizer = LLMSummarizer(MockLLMClient(["summary"]))

    with pytest.raises(ValueError):
        summarizer.summarize(None, [])
    with pytest.raises(ValueError):
        summarizer.summarize(
            None, [Turn(session_id="window-1", user_input="hi")]
        )


def test_session_uses_llm_summarizer_for_compaction() -> None:
    client = MockLLMClient(["摘要一", "摘要二"])
    session = Session(
        "window-1",
        summarizer=LLMSummarizer(client),
        max_turns=2,
        recent_turns=1,
    )
    for index in range(1, 6):
        session.record_turn(make_turn(user_input=f"q{index}", answer=f"a{index}"))

    assert session.summary == "摘要二"
    assert [turn.user_input for turn in session.turns] == ["q5"]
    assert len(client.requests) == 2
    assert "（无）" in client.requests[0].messages[1].content
    assert "q1" in client.requests[0].messages[1].content
    assert "摘要一" in client.requests[1].messages[1].content
    assert "q3" in client.requests[1].messages[1].content
    assert "q4" in client.requests[1].messages[1].content


@pytest.mark.parametrize(
    "scripted",
    [
        "",
        ModelError("model down", {}),
        RuntimeError("unexpected"),
    ],
)
def test_summarizer_failure_keeps_history_and_turns(scripted) -> None:
    client = MockLLMClient([scripted])
    session = Session(
        "window-1",
        summarizer=LLMSummarizer(client),
        max_turns=2,
        recent_turns=1,
    )
    session.record_turn(make_turn(user_input="q1", answer="a1"))
    session.record_turn(make_turn(user_input="q2", answer="a2"))

    with pytest.raises(SummarizationError):
        session.record_turn(make_turn(user_input="q3", answer="a3"))

    assert session.summary is None
    assert [turn.user_input for turn in session.turns] == ["q1", "q2"]
    assert session.turn_count == 2

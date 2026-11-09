# min_agent

从零实现的最小可用 Agent：自己实现 Agent Loop、工具调用、Session 管理、Context 组装与滚动摘要，不依赖 LangGraph、OpenHands 等 Agent 框架。

- 运行时只使用 Python 标准库，真实模型通过 OpenAI 兼容的 `/chat/completions` 接口接入。
- 内置三个工具：`calculator`、Mock `search`、`todo`。
- 支持多个逻辑会话（如 `window-1`、`window-2`）在同一进程内切换，历史、摘要和待办彼此隔离。
- 对话超过最大轮次后自动压缩旧轮次为滚动摘要，Context 不会无限增长。
- 工具调用写入结构化 trace，不记录 API Key 和模型隐藏推理。

当前状态：P0–P5 已完成；`python -m pytest` 共 319 项测试通过（2026-11-09）。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `src/min_agent/` | Agent 运行时实现 |
| `src/min_agent/loop.py` | 核心 Loop：模型决策、工具执行、重试与终止 |
| `src/min_agent/session.py` | Session、SessionManager、滚动摘要与工具状态 |
| `src/min_agent/context.py` | 把 Session 历史组装成有界 Context |
| `src/min_agent/llm.py` | LLM 请求构造、真实/Mock 客户端、LLM 摘要器 |
| `src/min_agent/parser.py` | 严格解析模型输出为 `tool_call` 或 `final` |
| `src/min_agent/tools.py` | 工具注册、JSON Schema 子集校验、统一执行 |
| `src/min_agent/builtin_tools.py` | 三个内置工具的实现 |
| `src/min_agent/trace.py` | 工具调用 trace（JSONL / 内存 / 空实现） |
| `src/min_agent/cli.py` | 单进程命令行界面 |
| `tests/` | 与被测模块同名的单元测试与端到端测试 |
| `scripts/` | 真实接口冒烟脚本 |
| `docs/development_plan.md` | 需求、阶段计划与验收记录 |
| `experiments/` | 冒烟结果与 trace 留档 |
| `logs/` | CLI 运行时 trace（已被 Git 忽略） |
| `memory/` | 工作区文档目录，当前为空；不被运行时读取 |

## 运行方式

### 环境要求

- Python 3.11 及以上（实测 Python 3.12.10）。
- 运行自动化测试不需要网络和 API Key。
- 使用真实模型时需要可访问的 OpenAI 兼容接口。

### 安装

```bash
# 在项目根目录执行
python -m pip install -e ".[dev]"
```

如果不想安装，也可以在项目根目录直接指定源码路径：

```bash
# macOS / Linux / Git Bash
PYTHONPATH=src python -m min_agent

# Windows PowerShell
$env:PYTHONPATH="src"; python -m min_agent
```

### 配置真实 LLM

复制环境变量模板（或直接创建 `.env`），至少填写三项：

```dotenv
MIN_AGENT_LLM_BASE_URL=https://api.deepseek.com
MIN_AGENT_LLM_MODEL=deepseek-chat
MIN_AGENT_LLM_API_KEY=<你的密钥>
```

可选配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MIN_AGENT_LLM_TIMEOUT` | `30.0` | HTTP 超时秒数 |
| `MIN_AGENT_LLM_TEMPERATURE` | `0.0` | 采样温度，范围 0–2 |
| `MIN_AGENT_LLM_MAX_TOKENS` | 不设置 | 最大生成 token 数 |

约定与安全边界：

- `.env` 已被 `.gitignore` 忽略，不会进入版本库。
- 密钥只存在于 `LLMConfig` 和请求头中；不写入代码、测试、trace 或错误信息。
- `base_url` 末尾会自动拼接 `/chat/completions`；如果已经包含该路径则直接使用。
- 程序不会保存模型隐藏的完整推理链。`deepseek-flash` 等思考模式模型可能要求回传 `reasoning_content`，因此本项目冒烟默认使用 `deepseek-chat`。

### 启动 CLI

```bash
python -m min_agent
```

启动后默认创建并进入 `window-1`，输入普通文本即开始一轮对话。

```text
min_agent CLI：输入消息开始对话，/help 查看命令，/exit 退出。
当前会话：window-1
[window-1] 你： 用 calculator 计算 123*45
助手： 123*45 的结果是 5535。
[window-1] 你： /new window-2
已创建并切换到会话 window-2。
```

CLI 命令：

| 命令 | 作用 |
| --- | --- |
| `/new <session_id>` | 创建会话并切换；已存在时直接切换，历史不清空 |
| `/use <session_id>` | 切换到已有会话；未知 ID 不会改变当前会话 |
| `/sessions` | 列出全部会话，`*` 标记当前会话 |
| `/help` | 显示帮助 |
| `/exit`、`/quit` | 退出 |

CLI 参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--env` | `.env` | 环境变量文件路径；文件不存在时忽略 |
| `--trace` | `logs/agent-trace.jsonl` | 工具 trace 的 JSONL 输出路径 |
| `--no-trace` | 关闭 | 不写 trace 文件 |
| `--session` | `window-1` | 启动时创建并使用的会话 ID |
| `--max-tool-calls` | `5` | 单轮最多工具调用次数 |
| `--max-turns` | `20` | Session 最大完整轮次 |
| `--recent-turns` | `10` | 压缩后保留的近期完整轮次 |

示例：

```bash
python -m min_agent --session window-1 --max-turns 8 --recent-turns 3
python -m min_agent --no-trace
```

### 运行测试

```bash
python -m pytest
```

测试配置在 `pyproject.toml` 中，`pythonpath = ["src"]`，因此无需安装即可直接运行。`tests/conftest.py` 会在每个用例前清空真实 LLM 配置并拦截 socket 建连，保证测试不依赖网络或 API Key。

当前结果：

```text
319 passed
```

### 真实接口冒烟

```bash
# 只验证 P3 的请求构造与决策解析
python scripts/smoke_llm.py

# 验证完整 Loop：直接回复、三个工具、多工具汇总、双 Session
MIN_AGENT_LLM_MODEL=deepseek-chat python scripts/smoke_agent.py
```

结果写入 `experiments/`，例如：

- `experiments/2026-11-09-p5-final-smoke.json`
- `experiments/2026-11-09-p5-final-smoke-trace.jsonl`

脚本会检查结果和 trace 中是否出现 API Key，一旦发现就终止写入并返回非零退出码。

## 系统设计

### 总体架构

```text
用户输入
   │
   ▼
AgentCLI ──────────────► SessionManager ──────────► Session
   │                         │                       ├─ turns      完整轮次
   │                         │                       ├─ summary    滚动摘要
   │                         │                       └─ tool_state 工具状态（如 todo）
   │                         ▼
   └──────────────────► AgentLoop
                             │
                             ├─ build_turn_request(session, turn, registry)
                             │      │
                             │      ├─ build_turn_context(...)  ← context.py
                             │      └─ registry.schemas()       ← tools.py
                             │
                             ├─ LLMClient.complete(request)     ← llm.py
                             │
                             ├─ parse_decision(raw)             ← parser.py
                             │      ├─ final      → 完成轮次
                             │      └─ tool_call  → ToolExecutor
                             │                         │
                             │                         ├─ 参数 Schema 校验
                             │                         ├─ 执行 handler
                             │                         ├─ ToolResult
                             │                         └─ ToolTrace → TraceSink
                             │
                             ├─ 工具结果写回当前 Turn，再次请求模型
                             │
                             └─ record_turn(...) → 必要时滚动摘要 → LoopResult
```

### 模块职责

| 模块 | 职责 | 关键约束 |
| --- | --- | --- |
| `models.py` | 统一数据契约：`Message`、`ToolCall`、`ToolResult`、`ModelDecision`、`Turn` | 值对象不可变；轮次结束后不可再追加工具调用 |
| `errors.py` | `ErrorInfo` 纯数据错误载荷；`AgentError` 及其子类 | 运行期错误有稳定错误码，跨模块统一序列化 |
| `session.py` | 进程内会话、轮次历史、滚动摘要、工具状态 | 只接收已完成轮次；压缩事务性提交；按 `session_id` 隔离 |
| `context.py` | 历史轮次与进行中轮次的消息展开 | 工具调用与结果成对出现；摘要作为 system 消息 |
| `tools.py` | 工具注册、JSON Schema 子集校验、统一执行 | 未知工具/参数错误/内部异常都转为 `ToolResult` |
| `builtin_tools.py` | `calculator`、Mock `search`、`todo` | 不根据用户关键词替模型选工具 |
| `llm.py` | 请求构造、环境配置、OpenAI 兼容客户端、Mock 客户端、摘要器 | 密钥不参与 `repr`，不进入请求体、日志或错误详情 |
| `parser.py` | 把模型响应严格解析为一个决策 | 非法输出不降级为 `final` |
| `loop.py` | 核心循环、工具续判、上限、重试、异常收敛 | 一次响应只执行一个决策；所有已声明失败转为 `LoopResult.error` |
| `trace.py` | 工具调用结构化记录 | 不记录密钥、完整提示词或隐藏推理 |
| `cli.py` | 单进程命令行 | 不向用户暴露堆栈或密钥 |

### 一轮对话的完整流程

1. CLI 把当前输入交给 `AgentLoop.run(session, user_input)`。
2. 创建进行中的 `Turn`（状态 `open`）。
3. 构造请求：Session 历史 + 当前轮已发生的工具调用/结果 + 当前用户输入 + 工具 Schema。
4. 调用 LLM，并把原始响应严格解析为：
   - `final`：写入最终答案，`turn.complete(answer)`，再由 `session.record_turn(turn)` 落库；
   - `tool_call`：交给 `ToolExecutor.execute()`，结果写入当前 `Turn`，然后回到第 3 步再次请求模型。
5. 直接回复时不会执行任何工具；工具执行后必须再次交给模型判断是继续调用工具还是给出最终答案。
6. 单轮工具调用默认最多 5 次；malformed 输出默认最多重试 1 次。任一上限触发后终止本轮并返回结构化错误。
7. 正常完成时 `LoopResult.ok` 为真；失败时以 `LoopResult.error` 为准，不展示未完成的答案。

### 模型响应协议

模型每次响应只输出一个 JSON 对象，不允许 Markdown 代码块或前后缀说明。

直接回答：

```json
{"kind": "final", "thought_summary": "简短判断说明", "answer": "给用户的回答"}
```

调用工具：

```json
{
  "kind": "tool_call",
  "thought_summary": "需要精确计算",
  "tool_call": {
    "name": "calculator",
    "arguments": {"expression": "123*45"}
  }
}
```

约束：

- `kind` 只能是 `tool_call` 或 `final`。
- 一次响应只执行一个决策；需要多个工具时由 Loop 多轮往返完成。
- `thought_summary` 是可选简短摘要，只用于解析、展示与日志，不写回 Context，也不是隐藏推理链。
- 工具 Schema 同时出现在请求体的 `tools` 字段和系统提示的工具清单中；不重复写入每一轮历史。
- 是否为工具调用由模型基于 Schema 自主决定，代码中没有用户关键词路由。

### 三个内置工具

| 工具 | 能力 | 失败处理 |
| --- | --- | --- |
| `calculator` | 支持 `+ - * / // % **` 和括号，手写递归下降解析 | 非法表达式、除零、结果过大返回结构化错误；不使用 `eval` |
| `search` | 本地固定语料 + 确定性兜底，返回稳定 Mock 结果 | 不访问真实搜索服务；参数错误返回结构化错误 |
| `todo` | `add`、`list`、`complete`，数据存于 `Session.tool_state_for("todo")` | 完成不存在的待办返回明确错误；状态按会话隔离 |

### 异常处理

| 错误码 | 触发场景 | 处理方式 |
| --- | --- | --- |
| `config_invalid` | 缺少环境变量、地址/参数非法 | CLI 返回退出码 2，只提示变量名 |
| `model_request_failed` | 网络失败、HTTP 错误、响应结构异常 | 终止本轮，返回明确错误 |
| `output_parse_failed` | 空响应、非法 JSON、缺字段、未知决策类型 | 最多重试 1 次；耗尽后终止本轮 |
| `invalid_tool_arguments` | 参数缺失、类型错误、枚举/长度不符 | 转为失败 `ToolResult` 反馈模型，计入工具调用上限 |
| `tool_not_found` | 模型选择未注册工具 | 转为失败 `ToolResult` 反馈模型 |
| `tool_execution_failed` | 工具内部异常 | 转为失败 `ToolResult` 反馈模型 |
| `tool_limit_exceeded` | 单轮工具调用达到上限 | 立即终止本轮，返回明确错误 |
| `summarizer_failed` | 摘要器异常或返回空摘要 | 保留原始历史，轮次不写入 Session，`recorded=False` |
| `session_not_found` | 切换到不存在的会话 | CLI 提示先创建，不改变当前会话 |

### 工具 trace

每次工具执行都会产生一条 `ToolTrace`，字段包括：

```json
{
  "timestamp": "2026-11-09T04:22:25.570642+00:00",
  "session_id": "smoke-calculator",
  "turn_id": "e09fad1a90004ce4b985024adc1dbb63",
  "sequence": 1,
  "call_id": "call_...",
  "tool": "calculator",
  "arguments": {"expression": "123*45"},
  "ok": true,
  "status": "success",
  "duration_ms": 0,
  "error": null
}
```

CLI 默认追加写入 `logs/agent-trace.jsonl`，每行一条并立即 flush。成功和失败的调用都会记录；测试可使用 `MemoryTraceSink` 断言。

## Memory：召回时机与放置方式

这里的 memory 指 Agent 运行时的对话记忆，不是仓库里的 `memory/` 文档目录。运行时记忆全部在进程内，退出即丢失。

### 四类 memory

| 记忆层 | 存放位置 | 内容 | 是否进入模型 Context |
| --- | --- | --- | --- |
| 完整轮次（短期记忆） | `Session.turns` | 用户输入、工具调用与结果、最终答案 | 是，仅保留最近若干轮 |
| 滚动摘要（压缩记忆） | `Session.summary` | 被淘汰旧轮次的合并摘要 | 是，作为一条 system 消息 |
| 工具状态（结构化记忆） | `Session.tool_state` | 例如 `todo` 的待办列表和自增 ID | 否，模型通过再次调用工具读取 |
| 可观测记录 | `logs/agent-trace.jsonl` 或 `MemoryTraceSink` | 工具名、参数、状态、耗时、错误 | 否，仅用于审计和测试 |

模型隐藏推理不保存，也不进入 Context。

### 召回时机

召回不是“创建会话时一次性拼接”，而是按每次模型请求实时组装。

1. **创建会话时：不召回。**
   `SessionManager.create(session_id)` 只创建空的 `Session`；重复创建同一个 ID 返回现有会话，不重置历史、摘要或工具状态。

2. **切换会话时：不搬运数据。**
   `SessionManager.switch(session_id)` 只改变“当前会话”指针。下一次请求会读取被选中 Session 自己的 `turns`、`summary` 和 `tool_state`，不会混入其他会话内容。

3. **每次调用模型前：召回当前 Session 的记忆。**
   `AgentLoop.run()` 的每一轮 `while` 循环都会调用 `build_turn_request()`，后者读取：
   - `session.summary`（如果存在）；
   - `session.turns`（当前保留的完整轮次）；
   - 当前进行中 `turn` 的 `user_input` 和已发生的工具调用/结果；
   - `registry.schemas()`（三个工具的能力信息）。

   因此，一次用户请求内如果连续调用了多次工具，每次再次请求模型时都会重新召回“历史 + 当前轮最新工具结果”。

4. **每轮结束时：决定是否压缩。**
   `session.record_turn(turn)` 先尝试追加新轮次：
   - 未超过 `max_turns`（默认 20）：直接保留完整轮次；
   - 超过 `max_turns`：把“旧轮次”交给 `LLMSummarizer`，与已有 `summary` 合并，成功后只保留最近 `recent_turns`（默认 10）个完整轮次。

   也就是说，默认在第 21 个完整轮次写入时触发第一次压缩，把最早的 11 个轮次压成摘要，保留最近 10 个完整轮次。

5. **工具执行时：按需读取结构化状态。**
   `todo` 工具在真正执行 `add`/`list`/`complete` 时，才通过 `session.tool_state_for("todo")` 读取当前会话的待办状态。状态本身不会被预先塞进 Context。

6. **摘要失败时：不召回、不提交。**
   摘要器异常或返回空摘要时，`Session` 不修改 `summary` 和 `turns`，本轮以 `summarizer_failed` 终止，`LoopResult.recorded=False`，模型已生成但未落库的答案不会展示给用户。

### 放置方式

Context 的消息顺序是固定的，对应 `context.py` 中的 `build_turn_context()`：

```text
1. system  基础系统指令 + 工具清单 JSON + 响应协议
2. system  name="conversation_summary"：以下是本会话较早对话的摘要：...
           （仅当 session.summary 存在）
3. 近期完整轮次，按时间顺序展开：
   user        该轮用户输入
   assistant   tool_calls（当前轮该次工具调用）
   tool        tool_call_id 对应的工具结果 JSON
   ...
   assistant   该轮最终答案
4. user     当前用户输入
5. 当前轮已发生的工具调用/结果（展开方式同上）
6. user     纠错提示（仅 malformed 重试时临时追加，不写入 Session）
```

具体放置规则：

- **滚动摘要放在基础 system 消息之后、历史轮次之前。**
  摘要由旧对话派生，使用 system 角色表示“这是压缩后的会话背景”，不冒充新的用户指令。

- **工具调用与工具结果必须成对放置。**
  工具调用保留为 assistant 消息的 `tool_calls` 字段，工具结果紧随其后，通过 `tool_call_id` 对应。即使发生压缩，也不会只保留调用或只保留结果。

- **当前轮输入只出现一次。**
  进行中的轮次尚未写入 `Session`，因此 `build_turn_context()` 在历史之后显式追加 `turn.user_input`，而不是重复追加用户输入。

- **工具结果使用稳定 JSON 文本。**
  `tool_result_content()` 输出 `{"ok": true, "output": ...}` 或 `{"ok": false, "error": ...}`，模型和测试都可以直接读取。

- **工具 Schema 放在请求能力层，不放进每轮历史。**
  Schema 出现在 `LLMRequest.tools` 和系统提示的工具清单中；历史里只保留实际发生过的调用与结果，避免每轮重复膨胀。

- **`thought_summary` 不进入 Context。**
  它保存在 `ToolExchange` 中供解析、展示和日志使用，但历史展开时不会写回消息列表。

- **纠错提示只存在于紧接的重试请求。**
  malformed 输出重试时，`notices` 作为最后一条 user 消息追加；该提示不写入 Session，不会污染长期历史。

- **trace 和待办状态不直接进入 Context。**
  待办状态通过 `todo` 工具结果间接进入模型；trace 仅用于可观测性。

### 压缩规则与事务性

默认参数：

- `max_turns = 20`
- `recent_turns = 10`
- 两者必须满足 `recent_turns < max_turns`，可通过 CLI 的 `--max-turns` 和 `--recent-turns` 调整。

压缩流程：

1. 把新轮次追加到候选列表；
2. 如果候选轮次不超过 `max_turns`，直接提交；
3. 否则把 `candidate[:-recent_turns]` 交给摘要器，保留 `candidate[-recent_turns:]`；
4. 摘要器合并 `previous_summary` 与新压缩轮次，生成一份新摘要；
5. 只有摘要成功返回后，才同时替换 `session.summary` 和 `session.turns`；
6. 任一失败都保持原历史不变，并抛出 `SummarizationError`。

这样做的目的：

- Context 有明确上界，不会随对话无限增长；
- 旧信息不会直接丢弃，而是以摘要形式保留；
- 压缩失败不会出现“历史已被删除但摘要没生成”的半完成状态；
- 工具调用和结果始终成对，不会被拆成半轮。

### 隔离、上限与生命周期

- 所有记忆按 `session_id` 隔离：`SessionManager` 使用字典保存会话，`turns`、`summary`、`tool_state` 都是每个 `Session` 的实例字段。
- 同一进程内可以随时切回任一 Session 继续对话；切换不会清空任何状态。
- 单轮工具调用默认最多 5 次，防止模型在工具之间无限循环。
- 自动化测试不访问网络：`tests/conftest.py` 拦截 socket 建连并清空真实 LLM 配置。
- Session 不做磁盘持久化，进程退出后历史、摘要和待办全部丢失。
- 仓库根目录的 `memory/` 是工作区文档目录，用于沉淀决策、教训和下一步，不被 Agent 运行时读取。

### 一个例子

假设 `window-1` 已经完成 20 个轮次，第 21 个轮次刚刚得到最终答案：

1. `record_turn()` 发现候选轮次为 21，超过 20；
2. 取前 11 个轮次生成摘要，保留最近 10 个完整轮次；
3. 下一次用户提问时，Context 为：
   - system：基础指令 + 工具清单 + 响应协议；
   - system：滚动摘要（前 11 轮的压缩内容）；
   - 最近 10 个完整轮次；
   - 当前用户输入；
   - 当前轮已发生的工具调用/结果（如果有）。
4. 如果用户切到 `window-2`，下一次请求只读取 `window-2` 自己的摘要和轮次；待办状态也只在 `window-2` 调用 `todo` 时读取，不会出现 `window-1` 的任何内容。

## 测试与验收

| 测试文件 | 覆盖内容 |
| --- | --- |
| `tests/test_models.py` | 数据契约与不变量 |
| `tests/test_errors.py` | 结构化错误与错误码 |
| `tests/test_session.py` | Session 隔离、切换、重复创建、压缩事务性 |
| `tests/test_context.py` | Context 组装、工具调用成对、摘要放置 |
| `tests/test_tools.py` | 注册、Schema 校验、未知工具、参数错误、内部异常 |
| `tests/test_builtin_tools.py` | 三个工具的功能与边界 |
| `tests/test_llm.py` | 请求构造、配置、真实客户端、Mock 客户端、摘要器 |
| `tests/test_parser.py` | 合法与非法决策解析 |
| `tests/test_loop.py` | 直接回复、工具续判、上限、malformed 重试、异常收敛 |
| `tests/test_trace.py` | trace 字段与 JSONL 写入 |
| `tests/test_cli.py` | 命令、会话切换、错误输出、退出 |
| `tests/test_integration.py` | 双 Session、纯对话追问、工具结果追问、压缩追问 |
| `tests/test_dependencies.py` | 依赖检查，确认未引入 Agent 框架 |

已记录的验收结果：

- 自动化测试：319 项通过，无网络、无 API Key。
- 真实接口冒烟：DeepSeek `deepseek-chat`，6 组用例全部通过（直接回复、`calculator`、Mock `search`、`todo`、多工具结果汇总、双 Session）。
- 结果文件：`experiments/2026-11-09-p5-final-smoke.json`。
- trace 文件：`experiments/2026-11-09-p5-final-smoke-trace.jsonl`，共 12 条工具 trace，覆盖两个 Session，已确认不含密钥。

## 已知边界

本期只实现最小可用 Agent，以下内容明确不在范围内：

- Session 跨进程持久化与重启恢复；
- 真实搜索、真实天气接口；
- 多 Agent、并行工具调用、RAG、复杂压缩策略；
- GUI、用户认证、数据库、部署运维；
- 保存或回放模型隐藏推理链。

需求、阶段计划与逐项验收证据见 [`docs/development_plan.md`](docs/development_plan.md)。

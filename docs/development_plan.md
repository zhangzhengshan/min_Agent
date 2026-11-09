# 最小可用 Agent TODO 开发计划

## 1. 目标与已确认边界

从零实现一个可运行的 Python 最小 Agent，自行完成 Agent Runtime、基本 Loop、工具调用、Session 管理、Context 管理、异常处理、执行日志和测试，不使用现成 Agent 框架接管主流程。

已确认的实现边界：

- 使用一个真实 LLM 接口验证自主决策；自动化测试使用 Mock LLM，不依赖外部网络。
- 通过 CLI 和 `session_id` 区分“窗口 1/2”；Session 仅保存在当前进程内，不支持重启恢复。
- 必须实现且只实现三个基础工具：`calculator`、Mock `search`、`todo`。其中 `todo` 不是可选项。
- 模型输出中的“思考过程”指简短的思考摘要或决策说明，不获取、不保存模型隐藏的完整推理链。
- 最终交付是可运行代码、CLI、测试和工具执行日志；本文只规划最小实现，不规划产品化功能。

## 2. 核心约束

1. **Runtime 自主实现**：Loop、工具调度、输出解析、Session、Context 和压缩流程必须由本项目实现。禁止用 LangGraph、OpenHands、OpenClaw 或其他 Agent 框架替代；只允许使用 HTTP、数据校验、测试等基础库。
2. **三个工具缺一不可**：`calculator`、Mock `search`、`todo` 必须全部注册、全部可被 LLM 选择、全部有测试；不得用 `weather` 或 `read_docs` 替代 `todo`。
3. **Schema 驱动决策**：每个工具必须公开名称、描述和参数 Schema，并随模型请求提供；不能通过写死用户关键词来冒充 LLM 自主选工具。
4. **Session 完全隔离**：对话历史、压缩摘要和 Todo 状态均按 `session_id` 隔离；同一进程内可切换回任一 Session 继续对话。
5. **Context 有界且可用**：设置最大对话轮次，保留近期完整轮次；超限后把旧轮次压缩为滚动摘要。工具调用和结果必须归属于所在轮次，不能只保留半轮。
6. **测试确定可重复**：单元测试和集成测试使用 Mock LLM、Mock Search，不要求 API Key；真实接口冒烟验收单独执行，失败时不得声称整体验收通过。
7. **失败可观察**：模型接口、输出解析、参数校验和工具执行异常不得导致无提示崩溃；每次工具调用必须留有带 `session_id` 的 trace 或执行日志，日志不得记录 API Key。
8. **禁止范围膨胀**：不增加 GUI、用户认证、数据库、Session 持久化、真实搜索、多 Agent、并行工具、RAG、复杂压缩、部署运维等额外功能。新增事项若不能直接服务本文验收标准，就不进入本期。

## 3. 分阶段 TODO

### P0：工程骨架与统一数据契约

**状态**：已完成（2026-11-09）。

**TODO**

- [x] 建立最小 Python 工程、`src/min_agent/` 和 `tests/`，配置统一的测试命令。
- [x] 定义后续阶段共用的数据结构：消息、完整对话轮次、工具调用、工具结果、模型决策和结构化错误。
- [x] 明确模型决策只有两类：`tool_call` 或 `final`；两者都可携带简短 `thought_summary`。
- [x] 检查依赖清单，确认没有引入 Agent 框架。

**交给 P1 的交付物**

- 可导入的项目骨架和可运行的测试环境。
- 统一的数据结构与错误类型，避免 Session、工具和 Loop 各自定义不兼容格式。

**验收标准**

- [x] 测试命令能执行，基础数据结构与错误类型测试通过。
- [x] 工具结果能明确区分成功与失败，模型决策能明确区分工具调用与最终答案。
- [x] 依赖中不存在代替核心 Runtime 的 Agent 框架。

### P1：Session 与 Context 管理

**状态**：已完成（2026-11-09）。实现：`src/min_agent/session.py`、`src/min_agent/context.py`；测试：`tests/test_session.py`、`tests/test_context.py`。

**TODO**

- [x] 实现进程内 `SessionManager`：创建、按 `session_id` 获取、切换和继续对话；重复创建同一 ID 时返回现有 Session，不清空历史。
- [x] 每个 Session 独立保存完整轮次、滚动摘要和工具状态，不实现跨进程持久化。
- [x] 实现 Context 组装：系统指令、滚动摘要、近期完整轮次、当前用户输入；近期轮次中保留工具调用及工具结果。
- [x] 工具 Schema 作为当前模型请求的工具能力信息传入，不重复写入每轮历史；`thought_summary` 默认只用于解析和日志，不塞回 Context。
- [x] 实现最大轮次限制和基础压缩：每轮结束后检查，完整轮次超过 20 轮时，将较旧轮次并入一份滚动摘要，直至只保留最近 10 个完整轮次；两个数值可配置。
- [x] 为摘要器定义可替换接口，本阶段用 Fake Summarizer 测试，P3 再接入真实 LLM。

**交给 P2 的交付物**

- `session.py`、`context.py` 及对应测试。
- 可供工具读取的 Session 工具状态，以及可供 Loop 调用的 Context 构建接口。

**验收标准**

- [x] 创建 `window-1`、`window-2` 后，分别写入历史和状态，再切回原 Session，能取得各自历史且没有交叉污染。
- [x] 纯对话追问时，Context 包含当前 Session 的相关历史；另一个 Session 的同名或相似内容不会出现。
- [x] 带工具追问时，Context 能取得同一轮中的工具调用和工具结果，而不只是用户文本与最终答案。
- [x] 超过最大轮次后，旧轮次被滚动摘要替代，近期轮次完整保留，Context 不无限增长，工具调用与结果不会被拆散。
- [x] 摘要器失败时保留原始历史、终止本轮并返回明确错误；不得静默丢弃历史后继续调用模型。
- [x] 重复创建已有 `session_id` 后，原历史、滚动摘要和工具状态保持不变。

### P2：工具注册、执行与三个必备工具

**状态**：已完成（2026-11-09）。实现：`src/min_agent/tools.py`（Tool Registry、Tool Executor、JSON Schema 子集校验）、`src/min_agent/builtin_tools.py`（三个工具与默认注册表）；测试：`tests/test_tools.py`、`tests/test_builtin_tools.py`。

**TODO**

- [x] 实现 Tool Registry：注册工具名称、描述、参数 JSON Schema 和处理函数；支持列出供 LLM 使用的 Schema。
- [x] 实现 Tool Executor：按名称查找工具、校验参数、执行处理函数、返回统一工具结果；拒绝未注册工具和重复注册。
- [x] 实现 `calculator`：支持基础算术，非法表达式和除零返回结构化错误，禁止直接使用 `eval`。
- [x] 实现 Mock `search`：根据查询返回稳定、可断言的模拟结果，不访问真实搜索服务。
- [x] 实现 `todo`：至少支持 `add`、`list`、`complete`，数据按 `session_id` 隔离；完成不存在的待办时返回明确错误。

**交给 P3 的交付物**

- `tools.py`（注册与执行）、`builtin_tools.py`（三个工具实现）及对应测试。
- 可直接提供给模型提示或工具调用的 Schema 列表，以及统一的工具执行接口。

**验收标准**

- [x] Registry 中同时存在 `calculator`、`search`、`todo`，每个工具都有非空名称、描述和参数 Schema。
- [x] LLM 请求构造器能够读取这三个工具的 Schema；后续集成测试不得通过关键词规则代替模型选择。（P2 提供 `ToolRegistry.schemas()`，测试已验证三个 Schema 可序列化；P3 请求构造器直接消费。）
- [x] `calculator` 正确计算合法表达式，非法表达式、错误参数和除零均有可断言结果。
- [x] Mock `search` 对固定查询返回固定结果，测试全程无外部网络依赖。
- [x] `todo` 能新增、列出和完成待办；`window-1` 的待办不会在 `window-2` 中出现。
- [x] 未知工具、重复注册、参数缺失、参数类型错误和工具内部异常均被捕获并转为统一结果。

### P3：LLM 接入、Schema 提示与输出解析

**状态**：已完成（2026-11-09）。实现：`src/min_agent/llm.py`（请求构造、配置与环境文件加载、真实/Mock 客户端、LLM 摘要器）、`src/min_agent/parser.py`（严格决策解析）；测试：`tests/test_llm.py`、`tests/test_parser.py`；冒烟脚本：`scripts/smoke_llm.py`。真实冒烟已通过：DeepSeek `deepseek-flash`，问候返回 `final`，计算请求选择 `calculator` 并得到 5535，结果见 `experiments/2026-11-09-llm-smoke.json`。本地凭据保存在已忽略的 `.env`，未写入代码、测试或日志。

**TODO**

- [x] 定义最小 `LLMClient` 接口，实现一个可配置的真实模型适配器和一个可按脚本返回响应的 `MockLLMClient`。
- [x] 构造模型请求时传入当前 Context，以及 Registry 中三个工具的名称、描述和参数 Schema。
- [x] 实现严格输出解析：提取 `thought_summary`，并识别一个 `tool_call`（工具名和参数）或一个 `final`（最终答案）；一次响应只执行一个决策。
- [x] 对空响应、非法 JSON、缺少必需字段、未知决策类型和工具参数不符合 Schema 返回可处理的解析或校验错误。
- [x] 将 P1 的摘要器接入同一 LLMClient；Mock 测试不调用真实接口。
- [x] 真实接口的地址、模型名和密钥通过配置注入，密钥不写入代码、测试或日志。

**交给 P4 的交付物**

- `llm.py`、`parser.py`、提示构造逻辑及对应测试。
- 可替换的真实/Mock LLMClient、工具 Schema 请求格式和统一模型决策对象。

**验收标准**

- [x] Mock 返回直接答案时，解析器得到 `final` 和答案；返回工具调用时，得到工具名、参数及 `thought_summary`。
- [x] 非法 JSON、缺字段、未知类型和 Schema 不匹配均返回明确错误，不把 malformed 输出当成最终答案。
- [x] 请求构造测试中可断言三个工具 Schema 均已提供给 LLM，且包含当前 Session 的 Context。
- [x] 使用真实模型做基础冒烟：明确问候可直接回复，明确计算请求能选择 `calculator`；该结果单独记录，不用 Mock 结果替代。执行方式：在 `.env` 或环境变量中设置 `MIN_AGENT_LLM_BASE_URL`、`MIN_AGENT_LLM_MODEL`、`MIN_AGENT_LLM_API_KEY` 后运行 `python scripts/smoke_llm.py`，结果写入 `experiments/YYYY-MM-DD-llm-smoke.json`。2026-11-09 已用 DeepSeek `deepseek-flash` 通过。

### P4：核心 Loop、异常处理、Trace 与 CLI

**状态**：已完成（2026-11-09）。实现：`src/min_agent/loop.py`（核心循环、工具续判、调用上限、malformed 重试与异常收敛）、`src/min_agent/trace.py`（结构化 `ToolTrace`、内存/JSONL/空实现）、`src/min_agent/cli.py` 与 `src/min_agent/__main__.py`（单进程 CLI）；`context.py`/`llm.py` 增加进行中轮次的上下文与请求构造。测试：`tests/test_loop.py`、`tests/test_trace.py`、`tests/test_cli.py`；全量 `python -m pytest` 315 项通过（新增 48 项 P4 用例）。CLI 入口：`python -m min_agent`。真实 Loop 冒烟已通过：DeepSeek `deepseek-flash`，要求用 `calculator` 计算 `123*45` 时 Loop 选择工具并返回 5535，结果见 `experiments/2026-11-09-p4-loop-smoke.json`，trace 见 `experiments/2026-11-09-p4-loop-smoke-trace.jsonl`（已确认不含密钥）。

实现约定：`AgentLoop.run()` 返回 `LoopResult`，调用方以 `error` 为准；摘要失败时模型答案已生成但轮次未写入 Session，`recorded=False`，不向用户展示该答案。工具 trace 默认由 CLI 写入 `logs/agent-trace.jsonl`（已加入 `.gitignore`）。

**TODO**

- [x] 串起基本循环：接收用户输入 → LLM 判断直接回复或调用工具 → 执行工具 → 将工具结果写回当前 Session → LLM 判断继续调用工具或返回最终答案。
- [x] 直接回复时不得执行工具；工具执行后必须再次交由 LLM 判断，不能固定返回工具原始结果。
- [x] 设置单轮最大工具调用次数（默认 5 次）和最多 1 次 malformed 输出重试，防止死循环；该限制与 P1 的最大对话轮次分别配置。
- [x] 处理模型请求失败、解析失败、未知工具、参数错误、工具异常、摘要失败和达到调用上限；malformed 输出按既定次数重试，未知工具、参数错误和工具异常可将结构化错误反馈模型并受调用上限约束，模型请求失败或摘要失败则终止本轮并返回明确信息。
- [x] 为工具调用记录结构化 trace：时间、`session_id`、轮次 ID、序号、工具名、参数、成功/失败、耗时和错误信息；不记录密钥。
- [x] 实现单进程 CLI：创建 Session、通过 `/use <session_id>` 切换、继续当前对话和退出；切换不会清空任何 Session。

**交给 P5 的交付物**

- `loop.py`、`cli.py`、日志记录及对应测试。
- 一个可运行的最小 Agent，可使用真实或 Mock LLM，并能在两个逻辑窗口间切换。

**验收标准**

- [x] 直接回复、单次工具调用、连续多次工具调用后回复三条路径均有通过测试。
- [x] 工具结果会进入下一轮模型输入；模型可据此继续调用工具或生成最终答案，证明 Loop 的继续/停止判断有效。
- [x] malformed 输出最多重试 1 次；工具调用达到上限后停止并给出明确结果，不会无限循环。
- [x] 每类已声明异常都有测试，程序不无提示崩溃，不向用户直接暴露堆栈或密钥。
- [x] 成功和失败的工具调用都有完整 trace，能通过 `session_id` 和轮次 ID 串联同一次任务。
- [x] CLI 中从 `window-1` 切到 `window-2` 再切回，能接着各自上下文对话，历史和 Todo 不混用。

### P5：完整测试与最终验收

**TODO**

- [ ] 补齐单元测试和端到端集成测试，测试文件按被测模块命名；不追求与核心要求无关的覆盖率数字。
- [ ] 使用 Mock LLM 和 Mock Search 跑完全部自动化测试，确保无网络、无 API Key 时结果稳定。
- [ ] 使用真实 LLM 做最终冒烟：直接回复、调用 `calculator`、调用 Mock `search`、调用 `todo`、根据工具结果生成最终答案。
- [ ] 执行双 Session 场景：`window-1` 查询模拟天气并记待办，`window-2` 写周报并记待办，随后分别切回并追问待办和前文内容。
- [ ] 分别验证纯对话追问、基于工具结果的追问、超最大轮次后的压缩追问。
- [ ] 对照下方硬性要求矩阵逐项验收；任何一项失败，都不能把本期标记为完成。

**最终交付物**

- `src/min_agent/` 中自主实现的 Runtime、三个工具、Session、Context、LLM 适配器、CLI 和日志。
- `tests/` 中与核心模块对应的单元测试、集成测试及可重复执行的测试命令。
- 真实接口冒烟结果和本计划验收勾选项；不额外制作超出项目使用所需的文档。

**验收标准**

- [ ] 全部自动化测试通过，真实接口冒烟通过；若因缺少凭据未执行真实冒烟，必须明确标记“尚未完整验收”。
- [ ] 双 Session 场景中，`window-1` 只出现天气相关待办，`window-2` 只出现周报相关待办，切回后均能续聊。
- [ ] 纯对话追问能引用本 Session 前文；带工具追问能引用先前工具结果并继续调用工具。
- [ ] 超限对话产生滚动摘要，近期完整轮次和关键历史状态仍可用于追问。
- [ ] 下方硬性要求矩阵全部有对应交付物和通过的验收用例。

## 4. 硬性要求验收映射

| 原始硬性要求 | 负责阶段 | 最低验收证据 |
| --- | --- | --- |
| 核心 Runtime 从零实现，不依赖 Agent 框架 | P0、P4、P5 | 依赖检查；Loop、调度、Session、Context 均为本项目代码 |
| 接收输入并判断直接回复或工具调用 | P3、P4 | `final` 与 `tool_call` 两条集成测试 |
| 执行工具后判断继续 Loop 或返回 | P4 | 单次和连续工具调用测试；工具结果再次进入模型输入 |
| 至少三个工具，且 `todo` 必须实现 | P2、P5 | `calculator`、Mock `search`、`todo` 均有功能测试和真实模型冒烟 |
| 工具包含名称、描述、参数 Schema | P2、P3 | Registry 测试；请求中可断言三个完整 Schema |
| LLM 基于 Schema 自主决策 | P3、P5 | 无关键词路由；真实模型在明确场景中选择目标工具 |
| 解析思考摘要、工具调用或最终答案 | P3 | 合法与非法输出的解析测试 |
| 窗口 1/2 为独立 Session，可随时续聊 | P1、P4、P5 | 双 Session 切换测试；历史、摘要、Todo 均无串扰 |
| 最大轮次限制与历史状态记忆 | P1、P5 | 超限测试、切回 Session 后续聊测试 |
| 支持纯对话追问和带工具追问 | P1、P5 | 两类独立集成用例，带工具用例包含先前工具结果 |
| Context 选择合适信息并进行基础压缩 | P1、P3、P5 | Context 内容断言；滚动摘要与近期完整轮次测试 |
| 基本异常处理 | P2、P3、P4 | 模型、解析、参数、工具和上限异常用例 |
| 工具调用 trace 或执行日志 | P4、P5 | 成功与失败日志断言，含 Session、轮次、参数、状态和耗时 |
| 构建覆盖以上功能的测试用例 | P0 至 P5 | 自动化测试全通过，真实接口冒烟单独有记录 |

## 5. 完成定义

只有同时满足以下条件，最小可用 Agent 才算完成：

1. 三个必备工具和全部核心模块均可运行，基本 Loop 能依据工具结果继续或停止。
2. 双 Session、两类追问、最大轮次、基础压缩、异常处理和 trace 均有可重复验证的证据。
3. 自动化测试全部通过，真实 LLM 自主决策冒烟通过。
4. 未通过新增框架、关键词伪路由或额外产品功能来规避任何硬性要求。

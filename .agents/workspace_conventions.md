# 工作区规范（下钻）

本文件是 `AGENTS.md` 的详细规则；与入口冲突时以本文件的分类与命名细则为准。

## 1. 分类边界

| 类别 | 位置 | 说明 |
|---|---|---|
| Agent 实现 | `src/min_agent/` | 只放可运行、可被测试引用的生产代码 |
| 测试 | `tests/` | 与 `src/` 同构；用例与夹具按被测模块归位 |
| 生效契约 | `docs/` | 需求、设计、约束；需求变更必须同步到此处 |
| 技能源 | `skills/` | 唯一技能目录，每个技能一个 kebab-case 子目录；`Quickstart.md` 为安装引导 |
| 试验与草稿 | `experiments/` | 未验收的想法、原型、失败方案、调试输出 |
| 脚本 | `scripts/` | 可重复执行、不依赖未提交状态的工具 |
| 记忆 | `memory/` | 只写结论性内容：决策、教训、下一步、恢复入口 |

数据类目录（raw/processed）当前没有真实结构化数据，不预建；出现需要保留原始数据的场景时，回到本文件增补 `data/raw/`（只读）与 `data/processed/`（可写），再落地。

## 2. 命名规则

- 通用：文件名概括内容；目录已表达类型时省略 `ref`、`doc`、`notes` 等重复前缀；空格一律用 `-` 或 `_` 代替。
- 日志、会议、周报、复盘等过程记录以 `YYYY-MM-DD` 开头，如 `2026-11-08-loop-design-review.md`。不同日期的独立周报不算同一文档的历史版本。
- Python：模块与包用 `snake_case`；类用 `PascalCase`；私有实现用前导 `_`。
- 测试：`tests/test_<被测模块>.py`，层级镜像 `src/min_agent/`；夹具放 `tests/conftest.py` 或同目录 `_fixtures/`。
- 脚本：`scripts/<动词>_<对象>.py`，如 `scripts/install_skill.py`、`scripts/run_tests.py`。
- 技能目录：`skills/<skill-name>/`，kebab-case，内含 `SKILL.md`（必须）与按需的 `references/`、`agents/`。
- 文档：设计、需求等长期生效文档用稳定文件名（`requirements.md`、`design.md`）；无需日期，便于引用不被过期。
- 媒体：图片、视频命名为 `主题_版本简述.后缀`（如 `方案_蓝色封面.png`）；同一素材新版验证可用后，旧版移入同目录 `archive/`，保留旧文件名，重名时补日期或简述，并同步更新文档引用。图片与视频不纳入 Git 追踪，也不在 Git 恢复范围内。

## 3. 版本与生效规则

- 单个逻辑文档只保留一份当前生效文件，文件名稳定；内容在原文上迭代，本规范不管理可编辑文档的历史版本。
- 需求变更以 `docs/` 中当前契约为准，不能只停留在聊天或 `memory/`。
- 验收依据不明时，不用“最新”、版本号或时间戳自行定版，先确认。
- 实验通过验收后，代码并入 `src/` 与 `tests/`、结论并入 `docs/`，不保留两份。
- 仅当图片、视频、音频等媒体二进制确需保留旧版时，才在对应目录按需建 `archive/`；不因此移动或改写原始素材。

## 4. 已完成：技能目录归并（2026-11-08）

`Package/` 已并入 `skills/`，实际执行映射（逐项核对，无内容改动，9 个文件哈希与移动前一致）：

| 原路径 | 现路径 |
|---|---|
| `Package/workspace-architect/` | `skills/workspace-architect/` |
| `Package/git-versioning/` | `skills/git-versioning/` |
| `Package/Quickstart.md` | `skills/Quickstart.md`（保持与技能目录同级，原文相对路径继续成立） |
| `Package/` | 已移除（空目录） |

`.agents/skills/workspace-architect/` 为安装副本，未改动。未处理事项：无。

后续技能仍按此结构维护：新增技能放 `skills/<skill-name>/`，不再建立发布快照目录；需要分发时由 `scripts/` 打包，不手工复制目录。

## 5. 只读约定

只读仅通过 `AGENTS.md` 与本文件约束 Agent 行为，不改动任何系统权限、ACL、所有权或只读属性。必须绝对只读时由用户自行设置目录权限。

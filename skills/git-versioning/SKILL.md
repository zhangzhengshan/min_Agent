---
name: DS_git-versioning
description: >-
  Local Git version history for non-programming project documents. Use for explicit requests to save a version (存个版本、保存当前版本), view or compare versions, undo or redo a saved change, restore an earlier version, check/install Git, or initialize document version management. Execute through a delegated sub-agent. Not for ordinary file saves, content editing, remote synchronization, or image/video version snapshots.
---

# DS_git-versioning

让新手通过自然语言保存、查看和恢复项目文档版本。默认使用当前本地分支；不引入开发协作流程。

## 子代理执行

你是受委托的 Git 子代理，按主 Agent 的交接执行，不再委托。自行核对仓库、HEAD、工作区和暂存区；交接缺失、有歧义或与实际差异不符时，返回主 Agent 补充信息。只提交本阶段获授权的修改及必要原因，不猜测修改原因，也不因文件已经暂存就将其纳入。

## 基本约定

- 每个新执行会话在第一次 Git 操作前运行 `git --version`；需要 Git 2.28 或更高，缺失或过旧时阅读安装模块。
- 保存一个用户可理解的任务阶段；不强制按文件或细小改动拆分。
- 恢复、撤销、重做都新增提交，保留之前的完整提交历史；不使用 reset、amend 或 rebase 改写历史。
- 文本与文档型二进制文件保存 Git 快照；图片、视频及其归档全部排除追踪，具体规则见初始化模块。
- 不读取或提交 `.env`、密钥或凭据；保留与本次任务无关的文件改动和暂存状态。
- 默认只处理本地版本。不存在远程仓库时跳过远程规则；已有远程时只说明本次操作仍为本地操作，不自动 fetch、pull 或 push，也不配置远程。

## 自然语言交互

| 用户意图 | 清楚的说法示例 |
|---|---|
| 保存 | 存个版本、保存当前版本 |
| 查看 | 查看版本历史 |
| 比较 | 比较这两个版本 |
| 撤销 | 撤销刚才保存的版本 |
| 重做 | 重做刚才的撤销 |
| 恢复 | 将方案恢复到某个版本或日期 |

接受其他意思明确的自然语言。普通“保存文件”不等于保存版本；单独的“保存”“撤销”有歧义时，将待澄清信息返回主 Agent。只有收到用户明确要求或同意保存的授权，才创建阶段提交。

## 按需下钻

| 需求 | 阅读 |
|---|---|
| 检查或安装 Git | [install.md](references/install.md) |
| 初始化、接管仓库、追踪范围和媒体归档约定 | [setup.md](references/setup.md) |
| 保存阶段版本、提交信息、保护无关暂存内容 | [commit.md](references/commit.md) |
| 撤销、重做、恢复旧版 | [recovery.md](references/recovery.md) |
| 遇到冲突或未完成的 Git 操作 | [conflicts.md](references/conflicts.md) |
| 写入或更新 AGENTS.md 指引 | [agents-md.md](references/agents-md.md) |

只读查看不需要额外模块：使用 `git status --short`、`git log --oneline -20` 或指定路径的 `git diff`、`git diff --cached`、`git diff <a> <b> --stat -- <paths>`。查看内容差异前先列出路径并排除敏感文件。二进制差异只报告文件发生变化，不凭 Git 差异编造文档内容。只读请求不初始化仓库或创建提交。

## 完成

向主 Agent 返回结果、相关提交编号与说明、对应的撤销或重做说法，以及尚存的无关改动。不得为了得到干净状态清理用户其他工作。图片、视频不在 Git 恢复范围内；操作失败时如实说明，不声称已完成。

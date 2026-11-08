# min_agent

从零实现一个最小 Agent：agent loop、工具调用、session 管理、context 有效管理，以及配套测试用例；不依赖现成开发框架。最终交付为可运行的 Python 实现与通过的测试。

## 目录导航

| 目录 | 放什么 | 读写约定 |
|---|---|---|
| `src/min_agent/` | 最小 Agent 的实现代码：loop、工具调用、session、context | 可改 |
| `tests/` | 测试用例，目录结构镜像 `src/` | 可改 |
| `docs/` | 当前生效的需求、设计与约束 | 可改 |
| `skills/` | 唯一的技能源目录：每个技能一个 kebab-case 子目录；`Quickstart.md` 为安装引导 | 可改 |
| `.agents/skills/` | 安装到本项目供调用的技能 | 安装时改 |
| `.agents/workspace_conventions.md` | 分类、命名、版本与归档的下钻规范 | 可改 |
| `experiments/` | 未验收的试验、草稿、测试输出 | 可改 |
| `scripts/` | 自动化脚本（安装、打包、校验） | 可改 |
| `memory/` | 提纯后的决策、教训、下一步与临时进度 | 可改 |

## 红线

- 当前没有只读约定区；如需保护某目录，先在本文件声明为只读，仍仅靠文档约定约束，不改系统权限。
- 只读仅为文档约定，不得修改本机文件或文件夹的系统权限、访问控制、所有权、权限继承或只读属性；如需绝对的只读权限控制，请自行设置相应目录权限。
- 当前规范以 `.agents/workspace_conventions.md` 为准；`docs/` 尚无生效文档，建立后以其为需求与设计契约。草稿、聊天记录与旧版本不作为生效契约。
- 未验收的试验产物留在 `experiments/`，不得混入 `src/`、`tests/` 或 `docs/`。

## 新建、重命名或移动文件前

- 先读 `.agents/workspace_conventions.md`，遵守分类、命名、版本与归档规则。

## 版本管理

<!-- DS_git-versioning:begin -->
用户明确要求保存、查看、比较、撤销、重做或恢复项目版本时，委托一个 sub-agent 使用 `DS_git-versioning`，提供操作意图、仓库及文件范围、本阶段修改与必要原因、目标版本（如需）和已知需保留的其他工作，期间暂停相关编辑及其他 Git 写操作。
每完成一个阶段的任务，并且有文件改动、且尚未保存为版本的，询问一次：“本阶段已完成，要保存一个版本吗？”用户同意后再执行；查看、讨论和刚完成存档时不重复询问
<!-- DS_git-versioning:end -->

技能可访问位置：`.agents/skills/git-versioning/SKILL.md`。

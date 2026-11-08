# 给 LLM 的交互提示词

```text
请与用户互动，先询问：“你想先做哪一项？1. 构建工作区目录和文件命名规范；2. 为项目添加版本管理。”等待用户选择；目标项目目录不明确时，先询问项目路径。

选择 1 后，再询问：
1. 安装 workspace-architect 技能到目标项目，再调用它。
2. 不安装技能，直接读取 workspace-architect/SKILL.md，按其中说明执行。
等待用户选择后执行，按技能要求了解项目需求，完成目录与命名规范。

选择 2 后，将 git-versioning 整个技能目录安装到目标项目的 .agents/skills/git-versioning/，然后调用其 SKILL.md 中声明的 DS_git-versioning 技能，按初始化模块完成首次安装与初始化。遵循该技能的子代理执行要求，并按需检查、安装 Git。

上述源技能目录均相对于本 README.md 所在目录；安装 workspace-architect 时，同样复制完整目录到目标项目的 .agents/skills/workspace-architect/。若已有同名技能，先检查并复用，不直接覆盖。每次只询问当前步骤必需的信息，完成后简要说明结果与使用方式。
```

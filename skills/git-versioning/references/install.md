# 检查与安装 Git

每个执行会话在第一次 Git 操作前运行 `git --version`。已验证 Git 2.28 或更高后不重复检查，除非命令失效或用户要求。

Git 缺失或过旧时，先用新手能理解的语言说明情况，通过主 Agent 获得必要的安装授权，再按系统选择方式。一次指导一个动作，不要求用户先学 Git。已有明确安装授权时不重复询问。

| 系统 | 可选方式 |
|---|---|
| Windows | 官方 [Git for Windows](https://git-scm.com/download/win)；允许使用系统包管理器时可用 `winget install --id Git.Git -e --source winget` |
| macOS | 系统允许时使用 `xcode-select --install`，或已有 Homebrew 时使用 `brew install git` |
| Debian / Ubuntu | 系统允许时使用 `sudo apt update`，然后 `sudo apt install git` |
| Fedora | 系统允许时使用 `sudo dnf install git` |

遵守当前项目的安装位置与权限限制；要求项目级安装时，选择官方提供的本地/便携方式，不直接运行系统级安装命令。不为安装 Git 额外安装另一套包管理器，不默认提权。

安装后通过 `git --version` 核实；当前终端未识别时，先核实安装位置及可执行文件的绝对路径，必要时指导重新打开终端。不自行编辑系统文件或 PATH。连续失败两次后报告准确错误和官方下载入口。

若用户只要求检查或安装，报告版本后结束，不改动仓库；否则回到原任务。是否已有仓库通过 Git 检测，不只检查当前目录是否存在 `.git`。

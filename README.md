# 量子计算学习营环境部署

先配置 Python 环境，再接入已安装且可正常使用的 AI 编程助手。下文以 **Windows PowerShell** 为主，所有命令默认在**仓库根目录**执行。

## 目录

- [1. 配置 Python 环境](#1-配置-python-环境)
  - [1.1 安装 uv](#11-安装-uv)
  - [1.2 安装 Python 和项目依赖](#12-安装-python-和项目依赖)
  - [1.3 验证运行并打开 Notebook](#13-验证运行并打开-notebook)
- [2. 仓库结构](#2-仓库结构)
- [3. 配置 AI 编程助手](#3-配置-ai-编程助手)
  - [3.1 准备 MCP 密钥](#31-准备-mcp-密钥)
  - [3.2 选择 Skill](#32-选择-skill)
  - [3.3 支持的客户端与配置位置](#33-支持的客户端与配置位置)
  - [3.4 配置命令与检查](#34-配置命令与检查)
- [4. 按需查阅](#4-按需查阅)

## 1. 配置 Python 环境

### 1.1 安装 uv

> uv > 管理 Python 版本、虚拟环境和 Python 包的工具，无需提前安装 Python。

使用 [uv 官方安装命令](https://docs.astral.sh/uv/getting-started/installation/)：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装后重新打开 PowerShell，进入下载或克隆的仓库目录，并检查 uv。将下面的路径替换为自己的实际路径：

```powershell
Set-Location 'D:\workspace\quantum_computing_camp'
uv --version
```

### 1.2 安装 Python 和项目依赖

仓库已设置 **Python 3.14**，运行时使用[南京大学镜像](https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone/)，Python 包使用[清华镜像](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)，无需修改全局配置。

```powershell
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.tmp\uv-python'
uv python install 3.14
uv sync --upgrade-package pyqpanda3
```

`uv sync` 会创建项目环境、安装依赖并更新 `uv.lock`。pyqpanda3 不固定版本，使用清华镜像中与 Python 3.14、当前平台兼容的最新稳定版；以后更新时再次执行该命令。

日常通过 `uv run --locked` 使用已同步的环境，无需手动激活 `.venv`，也不会每次运行都升级包。为自己的练习添加其他包可用 `uv add 包名`。

重新打开终端后，安装或更新 Python 前需再次设置上面的 `UV_PYTHON_INSTALL_DIR`。

### 1.3 验证运行并打开 Notebook

检查解释器和实际安装版本：

```powershell
uv run --locked python -c "import sys; from importlib.metadata import version; print(sys.executable); print(sys.version); print('pyqpanda3', version('pyqpanda3'))"
```

解释器应位于本仓库 `.venv`，Python 为 `3.14.x`。

<details>
<summary>可选：运行两比特线路自检</summary>

以下线路在本地运行，无需云端密钥：

```powershell
@"
from pyqpanda3.core import QProg, H, CNOT, measure, CPUQVM
p = QProg(2)
p << H(0) << CNOT(0, 1) << measure([0, 1], [0, 1])
m = CPUQVM()
m.run(p, 1000)
counts = m.result().get_counts()
print(counts)
assert sum(counts.values()) == 1000 and set(counts) <= {'00', '11'}
"@ | uv run --locked python -
```

应输出 `00`、`11` 的抽样计数，总数为 1000。

</details>

打开课程 Notebook：

```powershell
uv run --locked jupyter lab
```

在浏览器中选择 `training_day1.ipynb` 或 `training_day2.ipynb`。若不确定内核，在单元格运行 `import sys; print(sys.executable)`，确认指向项目 `.venv`。云任务单元格另需本源量子云凭据，首次验证先运行本地模拟部分。

## 2. 仓库结构

主要文件和目录如下；标注“生成”的项目在部署后出现，并非仓库自带文件。

```text
quantum_computing_camp/
├── README.md                         # 环境部署入口
├── training_day1.ipynb                # 第一天学习示例
├── training_day2.ipynb                # 第二天学习示例
├── pyproject.toml                    # Python 依赖与 uv 镜像配置
├── uv.lock                           # 本次同步的依赖版本
├── .python-version                   # 默认 Python 3.14
├── .gitignore                        # 本地环境与生成文件的忽略规则
├── LICENSE                           # 仓库许可证
├── scripts/                          # MCP 与 Skill 配置脚本
│   └── prepare_qpanda3_ai_kits.py
├── tests/                            # 配置脚本的自动化测试
│   └── test_prepare_qpanda3_ai_kits.py
├── docs/pdf/                         # 本地课程参考资料（可选）
├── .venv/                            # 生成：项目 Python 虚拟环境
├── .tmp/                             # 生成：本地运行时、缓存和中间文件
│   ├── uv-cache/                     # uv 包缓存
│   └── uv-python/                    # .venv 依赖的 Python 运行时
├── .agents/                          # 生成：Codex Skill
├── .claude/                          # 生成：Claude Code Skill
├── .codex/                           # 生成：Codex 配置
├── .codebuddy/                       # 生成：CodeBuddy Skill
├── .gemini/                          # 生成：Gemini CLI 配置与 Skill
├── .opencode/                        # 生成：OpenCode Skill
└── .zcode/                           # 生成：ZCode 配置与 Skill
```

客户端目录按需生成，MCP 文件和 Skill 的具体位置统一见 3.3；脚本生成的项目配置和 Skill 已加入 `.gitignore`。`.tmp/uv-python/` 仍被 `.venv` 使用，环境使用期间请勿删除；脚本下载的 ZIP 和解压中间文件也放在 `.tmp/`，正常退出时清理。

## 3. 配置 AI 编程助手

脚本保留原版 8 个客户端入口：OpenCode、Claude Code、Codex、Gemini CLI、CodeBuddy Code、Cline CLI、Codewhale、ZCode Agent。支持项目级的默认写入当前仓库；Cline CLI、Codewhale 的 MCP 使用用户级配置，需显式加 `--user`。

### 3.1 准备 MCP 密钥

登录[本源量子控制台 API Key 页面](https://console.originqc.com.cn/zh/apikey)获取密钥。将下面命令中的 **`QPANDA3_API_KEY` 替换为实际 API Key**，无需创建环境变量。脚本会将密钥写入本地 MCP 配置，请勿分享或提交含真实密钥的命令和文件。

### 3.2 选择 Skill

> Skill > AI 助手读取的编程说明与参考资料；MCP > AI 助手连接外部工具服务的协议。

| 研究方向 | 推荐 Skill |
|---|---|
| 通用量子计算、量子线路和算法开发 | [OriginQ/pyqpanda3-skill](https://github.com/OriginQ/pyqpanda3-skill) |
| 量子机器学习、可微分量子电路、量子神经网络 | 优先使用 [OriginQ/pyvqnet-skills](https://github.com/OriginQ/pyvqnet-skills) |

脚本默认安装 QPanda3 通用开发 Skill，实际安装名为 `qpanda3-programming`。`pyvqnet-skills` 及其 Python 依赖需按上游说明另行安装。

### 3.3 支持的客户端与配置位置

以下路径相对仓库根目录，`~` 表示当前用户主目录。Skill 目录内均有 `SKILL.md`。

| 客户端／脚本参数 | MCP 配置位置 | Skill 位置 | 备注 |
|---|---|---|---|
| [OpenCode](https://opencode.ai/docs/config/)／`opencode` | `opencode.jsonc`，兼容已有 `opencode.json` | `.opencode/skills/qpanda3-programming/` | 项目级；优先使用已有 JSONC，其次 JSON |
| [Claude Code](https://code.claude.com/docs/en/mcp)／`claude` | `.mcp.json` | `.claude/skills/qpanda3-programming/` | 项目级；首次确认项目 MCP |
| [Codex](https://developers.openai.com/codex/mcp/)／`codex` | `.codex/config.toml` | `.agents/skills/qpanda3-programming/` | 项目级；需信任项目 |
| [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/)／`gemini` | `.gemini/settings.json` | `.gemini/skills/qpanda3-programming/` | 项目级；使用 Streamable HTTP 的 `httpUrl` |
| [CodeBuddy Code](https://www.codebuddy.ai/docs/cli/codebuddy-dir)／`codebuddy` | `.mcp.json`，兼容已有 `mcp.json` | `.codebuddy/skills/qpanda3-programming/` | 项目级；与 Claude 可能共用 MCP 文件 |
| [Cline CLI](https://docs.cline.bot/mcp/mcp-overview)／`cline` | `~/.cline/data/settings/cline_mcp_settings.json` | `~/.cline/skills/qpanda3-programming/` | 使用 `--user`；IDE 扩展通过其 MCP 设置面板导入 |
| [Codewhale](https://codewhale.net/en/docs/mcp)／`codewhale` | `~/.codewhale/mcp.json` | `~/.codewhale/skills/qpanda3-programming/` | 终端 AI 编程客户端；使用 `--user` |
| [ZCode Agent](https://zcode.z.ai/cn/docs/mcp-services)／`zcode` | `.zcode/config.json` | `.zcode/skills/qpanda3-programming/` | 项目级；用户级可显式使用 `--user` |

### 3.4 配置命令与检查

替换密钥后，**只执行自己所用客户端的命令**；每条均配置 MCP 并安装 Skill，不会安装客户端本身。

```powershell
# OpenCode：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare opencode -k "QPANDA3_API_KEY"

# Claude Code：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare claude -k "QPANDA3_API_KEY"

# Codex：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare codex -k "QPANDA3_API_KEY"

# Gemini CLI：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare gemini -k "QPANDA3_API_KEY"

# CodeBuddy Code：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare codebuddy -k "QPANDA3_API_KEY"

# Cline CLI：用户级，影响该用户的所有项目
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare cline -k "QPANDA3_API_KEY" --user

# Codewhale：用户级，影响该用户的所有项目
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare codewhale -k "QPANDA3_API_KEY" --user

# ZCode Agent：项目级
uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare zcode -k "QPANDA3_API_KEY"
```

配置后从仓库根目录启动客户端，或在桌面端打开本仓库并新建会话。按提示信任项目，在客户端的 MCP／Skill 面板确认 `qpanda3-runtime` 已连接、`qpanda3-programming` 可发现，再实际调用一次设备列表工具检查返回结果。

Claude Code、Codex、Gemini CLI 可在交互会话用 `/mcp` 查看连接；Codex 也可运行 `codex mcp list`，Cline CLI 可运行 `cline config mcp`，Codewhale 可运行 `codewhale mcp list`。**写入配置成功不代表客户端已连接**，完整连接需在自己使用的客户端中检查。

## 4. 按需查阅

<details>
<summary>macOS / Linux 安装环境</summary>

在 Bash/Zsh 中安装 uv，重开终端并进入仓库后，运行相同的 Python 安装和依赖同步命令：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd /你的路径/quantum_computing_camp
export UV_PYTHON_INSTALL_DIR="$PWD/.tmp/uv-python"
uv python install 3.14
uv sync --upgrade-package pyqpanda3
```

南大和清华镜像配置同样生效；后续脚本命令与 Windows 相同，直接替换 API Key 占位符。平台兼容性以 [PyPI 发行文件](https://pypi.org/project/pyqpanda3/#files)为准。

</details>

<details>
<summary>脚本高级参数与来源</summary>

```powershell
uv run --locked python scripts/prepare_qpanda3_ai_kits.py --help
```

默认项目级配置；其他目录可加 `--project-dir '实际路径'`，用户级配置需显式加 `--user`。`list` 可查看全部入口及作用域。重新配置保留其他服务的设置值，原有注释和排版不保留；旧路径的历史配置不自动删除。

脚本基于本源量子的[原始脚本](https://show.originqc.com.cn/prod/qcloudai/qpanda3-skill_MCP/prepare_qpanda3_ai_kits.py)改进。默认下载的 `qpanda3-programming` 分发包与 GitHub 仓库 `pyqpanda3-skill` 名称不同，内容不一定与 GitHub 当前版本完全一致。

</details>

<details>
<summary>常见问题</summary>

| 现象 | 处理 |
|---|---|
| 找不到 uv 或客户端命令 | 安装后重开终端，用 `Get-Command 工具名` 检查 PATH |
| 下载失败或源不符 | 检查网络及已有 `UV_*` 环境变量；它们可能覆盖项目镜像设置 |
| 无匹配的 pyqpanda3 包 | 确认 Python 为 3.14，并核对 PyPI 支持的平台 |
| MCP 报 401/403 | 确认命令中的占位符已替换为实际密钥，且密钥有效、有对应权限 |
| 看不到项目 Skill/MCP | 确认工作目录是仓库根目录、项目已受信任，然后新建会话 |
| Skill ZIP 下载失败 | 检查本源下载服务；Skill 下载不经过清华 PyPI |

</details>

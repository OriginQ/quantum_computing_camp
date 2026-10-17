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
- [4. 按主题学习 Notebook](#4-按主题学习-notebook)
- [5. 按需查阅](#5-按需查阅)

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

在浏览器中打开 `notebooks/day1/` 或 `notebooks/day2/`，按文件名前的编号学习，具体主题见[第 4 节](#4-按主题学习-notebook)。每份可独立从上到下运行；若不确定内核，在单元格运行 `import sys; print(sys.executable)`，确认指向项目 `.venv`。

## 2. 仓库结构

主要文件和目录如下；标注“生成”的项目在部署后出现，并非仓库自带文件。

```text
quantum_computing_camp/
├── README.md                         # 环境部署入口
├── notebooks/                        # 按主题拆分、保存运行输出的课程 Notebook
│   ├── day1/                         # 01–06：基础线路、模拟、云、传态、编码、QFT
│   └── day2/                         # 01–05：哈密顿量、变分、噪声、加法、Grover
├── pyproject.toml                    # Python 依赖与 uv 镜像配置
├── uv.lock                           # 本次同步的依赖版本
├── .python-version                   # 默认 Python 3.14
├── .gitignore                        # 本地环境与生成文件的忽略规则
├── LICENSE                           # 仓库许可证
├── scripts/                          # MCP 与 Skill 配置脚本
│   └── prepare_qpanda3_ai_kits.py
├── tests/                            # 配置脚本与课程 Notebook 的回归测试
│   ├── test_prepare_qpanda3_ai_kits.py
│   └── test_training_notebooks.py     # Notebook 独立运行、输出与教学结果检查
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

脚本保留原版 8 个客户端入口：OpenCode、Claude Code、Codex、Gemini CLI、CodeBuddy Code、Cline CLI、Codewhale、ZCode Agent。支持项目级的默认写入当前仓库；**本脚本**目前对 Cline CLI、Codewhale 仅提供用户级配置，需显式加 `--user`，这不表示客户端本身只能使用用户级配置。

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
| [OpenCode](https://opencode.ai/docs/config/)／`opencode` | `opencode.jsonc`，兼容已有 `opencode.json` | `.opencode/skills/qpanda3-programming/` | 项目级；同时处理本项目 `.opencode/` 内配置中的同名服务 |
| [Claude Code](https://code.claude.com/docs/en/mcp)／`claude` | `.mcp.json` | `.claude/skills/qpanda3-programming/` | 项目级；首次确认项目 MCP |
| [Codex](https://developers.openai.com/codex/mcp/)／`codex` | `.codex/config.toml` | `.agents/skills/qpanda3-programming/` | 项目级；需信任项目 |
| [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/)／`gemini` | `.gemini/settings.json` | `.gemini/skills/qpanda3-programming/` | 项目级；使用 Streamable HTTP 的 `httpUrl` |
| [CodeBuddy Code](https://www.codebuddy.ai/docs/cli/codebuddy-dir)／`codebuddy` | `.mcp.json`，兼容已有 `mcp.json` | `.codebuddy/skills/qpanda3-programming/` | 项目级；与 Claude 可能共用 MCP 文件 |
| [Cline CLI](https://docs.cline.bot/mcp/mcp-overview)／`cline` | `~/.cline/data/settings/cline_mcp_settings.json` | `~/.cline/skills/qpanda3-programming/` | 使用 `--user`；IDE 扩展通过其 MCP 设置面板导入 |
| [Codewhale](https://codewhale.net/en/docs/mcp)／`codewhale` | `~/.codewhale/mcp.json` | `~/.codewhale/skills/qpanda3-programming/` | 本脚本需 `--user`；默认路径不存在时保留已有 `.deepseek/` 回退 |
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

## 4. 按主题学习 Notebook

每份 Notebook 已保存一次独立内核执行的文本与图像输出，重新运行时抽样结果可能略有不同。原有文字、字符线路图和课程插图保留，并增加图像线路图或结果图。图像输出已嵌入 Notebook；绘图中间文件存于 `.tmp/`。

### 第一天：量子线路与基础算法

| 顺序 | Notebook | 内容 |
|---|---|---|
| 01 | [构建量子线路](notebooks/day1/01_circuit_construction.ipynb) | 用 OriginIR、OpenQASM、QProg 三种方式构建 Bell 线路 |
| 02 | [CPUQVM 本地模拟](notebooks/day1/02_local_simulation.ipynb) | 运行两比特线路，读取测量概率 |
| 03 | [量子云运行](notebooks/day1/03_quantum_cloud.ipynb) | 选择后端、提交线路和查询结果的可选云端流程 |
| 04 | [隐形传态与动态线路](notebooks/day1/04_teleportation.ipynb) | 条件校正、Bob 测量概率及经典位序说明 |
| 05 | [量子态制备与编码](notebooks/day1/05_state_encoding.ipynb) | 基础、角度、振幅、稀疏编码与态矢量 |
| 06 | [量子傅里叶变换](notebooks/day1/06_quantum_fourier_transform.ipynb) | 三比特 QFT；末尾附录含酉性与门分解推导、相位和干涉实验 |

### 第二天：期望值与进阶算法

| 顺序 | Notebook | 内容 |
|---|---|---|
| 01 | [哈密顿量与期望值](notebooks/day2/01_hamiltonian.ipynb) | Pauli 算符、理想与含噪声期望值及可选云端计算 |
| 02 | [变分量子线路](notebooks/day2/02_variational_circuits.ipynb) | 参数化 ansatz、经典优化与能量曲线 |
| 03 | [含噪声模拟](notebooks/day2/03_noise_simulation.ipynb) | 门噪声、读出噪声和 Bell 偶校验概率 |
| 04 | [量子加法器](notebooks/day2/04_quantum_adder.ipynb) | 半加器、全加器及默认 12 比特的 3 + 5 示例 |
| 05 | [Grover 搜索](notebooks/day2/05_grover_search.ipynb) | oracle、扩散算子及可选云端运行；末尾附录含二维旋转推导与迭代次数实验 |

量子云相关 Notebook 默认 `RUN_ON_QCLOUD = False`，对应单元格保存的是明确的跳过提示，**不代表云端任务已执行**。需要真机任务时，在启动 Jupyter 前准备 `APIKEY` 环境变量，再手动开启；后端可用性和任务结果以实际返回为准。原第二天末尾空白的“AI工具”主题统一见[第 3 节](#3-配置-ai-编程助手)。

## 5. 按需查阅

<details>
<summary>平台支持与 macOS / Linux 安装环境</summary>

截至 2026-10-17，[PyPI 当前 pyqpanda3 0.5.0 的发行文件](https://pypi.org/project/pyqpanda3/0.5.0/#files)对 CPython 3.14 提供以下 wheel；这表示发行包覆盖，不代表已在全部平台实测。安装命令仍默认获取兼容的最新稳定版。

| 平台 | Python 3.14 wheel |
|---|---|
| Windows x86-64 | 有 |
| Linux x86-64（glibc） | 有 |
| macOS 13+ Apple Silicon（arm64） | 有 |
| macOS Intel、Linux arm64／musl、Windows arm64 | 当前发行版未提供，也无源码包；不能直接套用此安装流程 |

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

默认项目级配置；其他目录可加 `--project-dir '实际路径'`，用户级配置需显式加 `--user`。`list` 可查看全部入口及作用域。重新配置保留其他服务，以及同名服务的启停、超时和工具过滤设置；更新连接地址及认证字段，原有注释和排版不保留。Skill 更新会替换整个对应目录，个人文件请放在其他目录。

`-k` 也支持本地 JSON 配置文件，可同时指定 MCP 配置和自定义 Skill 地址。

升级或卸载时留意：

- 相比原版，默认作用域改为项目级，项目根取当前目录或 `--project-dir`，不再取脚本目录。以前的用户级配置和旧 `.codex/skills/` 不自动迁移或删除；Codex 当前使用 `.agents/skills/`。显式指定客户端可预先生成配置，无需通过自动检测；卸载必须指定单个客户端，不再批量跨作用域清理。
- OpenCode 会同步更新本作用域兼容文件里已有的同名服务；ZCode 首次写入原生 MCP 时会保留原先有效的 `.agents/mcp.json` 服务；Claude 新建 `.mcp.json` 时保留 CodeBuddy 原 `mcp.json` 中的服务。Codewhale 不要求或改写 `constitution.json`。
- `uninstall 客户端参数` 只清理所选作用域兼容配置中的 `qpanda3-runtime` 和默认 Skill `qpanda3-programming`，自定义名称需手动处理。Claude 和 CodeBuddy 共用 `.mcp.json` 时，删除 MCP 会同时影响两者；加 `--skill-only` 可只卸载该客户端的默认 Skill。
- 检测到不支持的配置根覆盖（如 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`、`GEMINI_CLI_HOME`、`XDG_CONFIG_HOME`、`OPENCODE_CONFIG*`、`CLINE_*`、`CODEWHALE_HOME`）时，脚本会明确拒绝，请按客户端说明手动配置；CLI 单次覆盖或组织策略仍需在客户端核查。配置或 Skill 经过符号链接／目录联接时也需手动处理。
- 使用 `--api-key-env` 时，Claude、Codex、CodeBuddy 可引用环境变量；CodeBuddy 变量名必须匹配 `[A-Z_][A-Z0-9_]*`。本地 JSON 输入、自定义 Skill 路径或其他仓库不会自动获得本仓库的忽略保护，含密钥的输入建议放 `.tmp/`。

下载、解压和恢复备份存于仓库 `.tmp/`。配置先完整序列化，再用同目录的短期临时文件原子替换，以支持跨盘用户配置；Skill 复制或配置写入报错时尝试恢复旧内容。若恢复也失败，脚本会输出保留在 `.tmp/` 的恢复路径；Skill 复制不保证断电时的原子性。

脚本基于本源量子的[原始脚本](https://show.originqc.com.cn/prod/qcloudai/qpanda3-skill_MCP/prepare_qpanda3_ai_kits.py)改进。默认下载的 `qpanda3-programming` 分发包与 GitHub 仓库 `pyqpanda3-skill` 独立更新，不能假定两者逐文件一致。

Skill 的兼容基线以所下载包的 `SKILL.md` 与版本说明为准；本仓库的本地线路测试不能证明全部参考 API 与最新包一致。ZIP 会检查 CRC、路径和解压规模，但这不等同于来源认证或固定版本校验。

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

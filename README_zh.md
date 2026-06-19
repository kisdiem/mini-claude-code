# Mini Claude Code 中文说明

![Mini Claude Code 桌面软件截图](docs/images/desktop-app.png)

Mini Claude Code 是一个教学型 AI Agent 桌面项目，用来展示一个类 Claude Code 的 Agent 系统如何由模型、工具、调度、记忆、权限和运行时共同组成。

> Agent = Model + Tools + Orchestration + State / Memory + Runtime + Guardrails

## 项目定位

本项目适合用于向甲方、HR 或面试官展示以下能力：

- AI Agent 架构设计；
- 工具调用和权限控制；
- MCP、hooks、subagent、上下文压缩等工程模块；
- 桌面软件封装和本地运行体验；
- benchmark / report / trace / runtime evidence 的评测思路；
- 从原型迭代到工程化 runtime 的版本演进记录。

## 当前能力

### 桌面软件

- 类聊天软件的桌面窗口；
- 手动填写 API Key、base URL、模型和权限模式；
- 会话管理；
- 文件和图片附件；
- 运行日志展示；
- 本地封面和桌面图标；
- mock 模式，无需 API key 即可演示。

### Agent Runtime

- S20 策略层；
- Planner / Executor / Verifier 分层；
- 权限策略和 permission ledger；
- evidence ledger；
- task contract；
- subagent state machine；
- event history / replay；
- context budget 和 conversation compaction；
- tool failure recovery；
- tool-use eval harness。

### Subagent

- 独立 task contract；
- 独立 session / memory / hook / MCP 配置；
- worktree isolated writer；
- parallel writer；
- DAG task scheduler；
- teammate communication；
- quality gates 和 merge gates。

### MCP / Hooks

- MCP registry；
- capability index；
- tool description quality layer；
- dynamic tool retrieval；
- resource / prompt governance；
- OAuth / token / secret governance；
- hook runtime timeout、retry、failure mode、schema validation；
- session、prompt、tool、permission、subagent、task、context、workspace 事件覆盖。

## 快速开始

### 1. 安装依赖

推荐的开发安装方式：

```powershell
cd mini-claude-code
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

如果只是本地演示，也可以只安装运行依赖：

```powershell
python -m pip install -r requirements.txt
```

### 2. 运行 mock 演示

mock 模式不需要 API key，适合给甲方快速展示。

```powershell
python -m mini_cc --mock --s20 --permission auto --workspace . "你好，列出当前项目结构"
```

### 3. 启动桌面软件

Windows 一键启动方式：

```text
双击 scripts\start_desktop.bat
```

PowerShell 启动方式：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_desktop.ps1
```

也可以直接运行：

```powershell
python -m mini_cc.desktop_launcher
```

### 4. 使用真实模型

在桌面软件的设置中填写：

- Provider；
- API Key；
- Base URL；
- Model；
- Permission；
- Runtime budget。

API Key 只保存在本机 `.mini_cc/desktop-settings.json`，该目录已加入 `.gitignore`，不会提交到 GitHub。

## Coding Task Success Loop

Coding Task Success Loop 可以理解成“代码任务成功闭环”。它解决的问题是：agent 不能只会改文件，还必须在改完后运行真正的测试或检查命令，测试失败还要继续最小修复并再次验证。

开启后，runtime 会跟踪：

- 是否修改了代码；
- 修改了哪些文件；
- 是否运行过真实验证命令；
- 最后一次验证是否通过；
- 验证失败后是否做过最小修复。

示例命令：

```powershell
py -3 -m mini_cc --s20 --coding-loop --permission auto --workspace . "fix the failing test"
```

带明确测试命令的 harness 运行：

```powershell
py -3 -m mini_cc run --s20 --coding-loop --test-command "python -m unittest discover" --permission-mode bypass --workspace . --output-format json --prompt "fix the bug"
```

pytest 示例：

```powershell
python -m mini_cc --s20 --coding-loop --test-command "python -m pytest" --workspace . "fix the failing tests"
```

轻量 task-success eval：

```powershell
python -m mini_cc.evals.task_success
```

关键规则：

- `apply_patch` 用于稳定代码编辑，比复杂场景下的 `replace_text` 更不容易因为旧字符串匹配失败而卡住；
- 只有 `run_shell` 里看起来像测试/检查的命令才算 verification；
- `git_diff` 只能说明“改了什么”，不能说明“代码可用”；
- `git_status` 只能说明工作区状态，不能说明“代码可用”；
- `context_snapshot` 只能说明上下文，不是测试通过证据；
- 每次运行会写出 `.mini_cc/task-success/last-run.json`，方便向甲方展示本次代码任务是否验证通过。

重要区别：

- Runtime Evidence（运行证据）：说明 Agent 看过什么、收集过什么，比如 `git_diff`、`git_status`、`context_snapshot`、`list_files`、`read_file`、`search_text`。
- Code Verification（代码验证）：说明代码真的跑过测试、lint、类型检查或构建检查，并且命令成功退出。
- 对代码修改任务来说，只有通过 `run_shell` 执行真实测试/检查命令并成功退出，才算代码验证通过。
- `CodingLoopPolicy` 是代码任务成功验证的强判定来源；运行证据可以辅助报告，但不能替代测试通过。

详细架构见：`docs/coding_reliability_loop.md`。

## 阶段化 Coding Task State Machine

`TaskStateMachine` 把代码任务从普通的“模型想调什么工具就调什么工具”，升级成按阶段推进：

```text
INTAKE -> EXPLORE -> LOCALIZE -> PLAN -> EDIT -> VERIFY -> REPAIR -> FINAL
```

它不是只靠 prompt 提醒模型，而是在工具执行前做硬性检查：

- 没有先探索项目时，不能直接写文件；
- 没有读取目标文件时，不能修改这个文件；
- 没有写出 `planned_files` 计划时，不能进入编辑；
- 修改只能落在计划文件内，除非任务本身明确要求新增文件；
- 修改后必须通过 `run_shell` 跑真实测试、lint、类型检查或构建检查；
- 验证失败后进入 REPAIR，只允许做最小修复并重新验证。

简单说：`TaskStateMachine` 管“过程有没有按正确阶段走”，`CodingLoopPolicy` 管“最后有没有真实验证通过”。

## 生产可用性

本项目已经补充了面向交付的基础工程能力：

- `pyproject.toml`：支持 `pip install -e .` 安装；
- GitHub Actions CI：自动安装依赖并运行测试；
- `scripts/health_check.ps1`：Windows 一键健康检查；
- `.gitignore`：默认排除本地 API key、日志和运行状态；
- `docs/production-readiness-zh.md`：生产可用性评估和限制说明。

## 常用演示命令

### 工具调用演示

```powershell
python -m mini_cc --mock --s20 --permission auto --workspace . "读取 README 并总结项目能力"
```

### runtime report

```powershell
powershell -ExecutionPolicy Bypass -File scripts/runtime_report.ps1
```

### tool-use eval

```powershell
powershell -ExecutionPolicy Bypass -File scripts/tool_use_eval.ps1
```

### Terminal-Bench smoke

```powershell
powershell -ExecutionPolicy Bypass -File scripts/terminal_bench_smoke.ps1
```

## 项目结构

```text
mini_cc/
  agent.py              Agent 主循环
  tools.py              工具运行器
  permission.py         权限分类和策略
  workflow.py           Planner / Executor / Verifier
  subagents.py          subagent runtime
  hooks.py              hook 事件和运行时
  mcp.py                MCP adapter
  context.py            上下文压缩和预算
  tool_eval.py          工具能力评测
  tool_runtime.py       runtime report
  desktop_app.py        桌面软件界面

docs/                  架构、差距分析和生产可用性文档
examples/              hook / MCP / subagent / permission 示例
scripts/               一键演示和健康检查脚本
tests/                 自动化测试
frontend/              Web 展示版
```

## 测试

```powershell
python -m unittest discover
```

当前本地验证结果：

```text
Ran 255 tests
OK
```

## 给甲方看的重点

建议展示顺序：

1. 打开桌面软件，展示聊天式入口和设置面板。
2. 用 mock 模式跑一个无 API key 的任务。
3. 展示 `README_zh.md` 里的架构说明。
4. 展示 `docs/architecture.md` 和 `docs/production-readiness-zh.md`。
5. 展示 `VERSION_HISTORY.md`，说明项目按版本持续迭代。
6. 跑 `scripts/health_check.ps1`，证明环境和核心能力可验证。
7. 跑 `python -m unittest discover`，证明不是只有界面。

## 已知限制

- 这是教学型和原型工程项目，不是 Claude Code 官方产品。
- 真实模型效果依赖用户配置的 API endpoint 和模型能力。
- 部分 MCP live smoke 需要真实 MCP server 和登录凭据。
- Terminal-Bench / SWE-bench 全量评测需要 Docker、磁盘空间和较长运行时间。
- 桌面软件目前以 Tkinter 实现，视觉效果仍可继续向 Electron / Tauri / WPF 级别升级。

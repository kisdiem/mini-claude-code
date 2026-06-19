# Mini Claude Code 中文说明

Mini Claude Code 是一个教学型 AI Agent 桌面项目，用来展示一个类 Claude Code 的 Agent 系统应该如何由模型、工具、调度、记忆、权限和运行时共同组成。

这个项目不是简单聊天壳。它重点展示的是：

> Agent = Model + Tools + Orchestration + State / Memory + Runtime + Guardrails

## 项目定位

本项目适合用于向甲方或面试官展示以下能力：

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
- 可收起的执行过程；
- 本地封面和桌面图标；
- 支持 mock 模式，无需 API key 即可演示。

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
- 事件覆盖：session、prompt、tool、permission、subagent、task、context、workspace。

## 快速开始

### 1. 安装依赖

```powershell
cd mini-claude-code
python -m pip install -r requirements.txt
```

### 2. 运行 mock 演示

mock 模式不需要 API key，适合给甲方快速展示。

```powershell
python -m mini_cc --mock --s20 --permission auto --workspace . "你好，列出当前项目结构"
```

### 3. 启动桌面软件

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

注意：API Key 只保存在本机 `.mini_cc/desktop-settings.json`，该目录已加入 `.gitignore`，不会提交到 GitHub。

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

docs/                  架构和差距分析文档
examples/              hook / MCP / subagent / permission 示例
scripts/               一键演示脚本
tests/                 自动化测试
frontend/              Web 展示版
```

## 测试

```powershell
python -m unittest discover
```

当前本地验证结果：

```text
Ran 220 tests
OK
```

## 给甲方看的重点

建议展示顺序：

1. 打开桌面软件，展示聊天式入口和设置面板。
2. 用 mock 模式跑一个无 API key 的任务。
3. 展示 `README_zh.md` 里的架构说明。
4. 展示 `docs/architecture.md`。
5. 展示 `VERSION_HISTORY.md`，说明项目是按版本持续迭代的。
6. 跑 `python -m unittest discover`，证明不是只有界面。
7. 跑 runtime report 或 tool-use eval，说明项目有测试和证据意识。

## 已知限制

- 这是教学型和原型工程项目，不是 Claude Code 官方产品。
- 真实模型效果依赖用户配置的 API endpoint 和模型能力。
- 部分 MCP live smoke 需要真实 MCP server 和登录凭据。
- Terminal-Bench / SWE-bench 全量评测需要 Docker、磁盘空间和较长运行时间。
- 桌面软件目前以 Tkinter 实现，视觉效果仍可继续向 Electron / Tauri / WPF 级别升级。

## 安全说明

- 不提交 API key；
- `.mini_cc/` 已加入 `.gitignore`；
- `.env` 已加入 `.gitignore`；
- 示例中只保留占位 key；
- 权限系统默认区分 read、verify、workspace_write、network、package_manager、docker、destructive 等风险。

## 适合继续优化的方向

- 打包成独立 Windows installer；
- 增加更完整的桌面 UI 设计系统；
- 增加真实 MCP server marketplace；
- 增加可视化 DAG / subagent trace；
- 增加在线 demo 或录屏；
- 补充 CI workflow；
- 发布 GitHub Release。


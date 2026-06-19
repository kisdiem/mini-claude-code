# Mini Claude Code 甲方检查说明

当前版本：`3.5.0`

这是一个 Claude Code 教学版工程，不是 Anthropic Claude Code 的产品级复刻。它的重点是展示 agent loop、工具治理、hooks、MCP、subagent、benchmark/reporting 等核心机制如何落到代码和可检查产物里。

## 5 分钟内可跑的演示

在项目根目录执行：

```powershell
.\scripts\start_desktop.ps1
.\scripts\start_frontend.ps1
.\scripts\mock_demo.ps1
.\scripts\tool_use_eval.ps1
.\scripts\runtime_report.ps1
.\scripts\terminal_bench_smoke.ps1
```

桌面也已经创建了 `Mini Claude Code.lnk`，可以像普通软件一样双击打开。

桌面版现在不要求用户手动设置 `turns` 和 `timeout`。软件会像聊天窗口一样按任务自动分配运行预算：

- 普通对话：短预算；
- 读取/搜索/总结：标准预算；
- 修改代码/运行测试：工程预算；
- benchmark、Docker、SWE-bench：长任务预算。

需要调试时，可以在“接口设置”里打开“显示执行过程/工具轨迹”。

前端地址：

```text
http://127.0.0.1:8765
```

可选真实 MCP/hook smoke：

```powershell
.\scripts\mcp_hook_live_validation.ps1
```

## 跑通看什么

- `.mini_cc/demo/mock-demo.txt`：mock agent 能启动并调用 S20 能力；
- `.mini_cc/tool-use-eval/tool-use-eval.md`：工具选择、参数、权限、hook、失败恢复等测试报告；
- `.mini_cc/tool-runtime-report/tool-runtime-report.md`：按证据打分的 runtime 报告，不再默认 100%；
- `.mini_cc/terminal-bench-smoke/report/terminal-bench-preflight.json`：Terminal-Bench smoke/preflight 记录；
- `.mini_cc/mcp-hook-live-3.3/mcp-hook-live-validation.md`：可选 MCP/hook live validation。

## 示例目录

- `examples/hooks`：hook 配置示例；
- `examples/mcp`：MCP registry 示例；
- `examples/subagents`：subagent 配置示例；
- `examples/parallel_writer`：并行写 worker 合并前需要的证据；
- `examples/permission_policy`：权限策略示例。

## 什么算成功

教学版成功标准不是“等同 Claude Code”，而是：

- demo 能在无 API key 的 mock 模式跑通；
- report 里能看到真实 artifact 路径；
- 缺证据时 report 会降分；
- subagent 写入合并前必须有 diff、evidence、verification；
- Terminal-Bench 至少能完成 smoke/preflight。

## 已知限制

- 没有达到 Claude Code 产品级 IDE/终端体验；
- 外部 benchmark 全量分数依赖 Docker、磁盘和网络环境；
- SWE-bench 之前的 97.18% 是 gold patch completed-sample resolved rate，不是本 agent 自主解题分数；
- MCP live validation 目前以本地可控 smoke 为主，真实第三方 MCP server 还需要接入更多样例。

# Subagent 2.0 优化方案

日期：2026-06-19

本文目标有三个：

1. 评估当前 `mini-claude-code` 在 subagent 方向和 Claude Code 的差距。
2. 规划从当前版本到 `2.0` 的分版路线，每次按 `0.1` 迭代。
3. 看看互联网上还有哪些公开设计，在某些维度上比 Claude 官方路线更适合我们借鉴。

## 0. 版本编号先说明

当前仓库刚完成的是 `1.10`。如果按十进制理解，它其实等价于 `1.1`。

为了满足“后续每版加 `0.1`”这个目标，我建议从下一版开始，版本号改成下面这种人类更容易理解的写法：

- `1.2`
- `1.3`
- `1.4`
- ...
- `2.0`

不要继续写成 `1.11`、`1.12` 这种形式，否则它更像“补丁号”，不直观表达“每次提升一个大台阶”。

## 1. 当前本地基线

我们现在已经有这些 subagent 相关能力：

- 独立 subagent prompt、tool allowlist、model override；
- subagent 私有 memory、私有 hooks、私有 session；
- 子会话 resume；
- 配置驱动 subagent；
- capability registry；
- 动态 planner，但有本地 schema 和 capability 校验；
- 只读并行；
- 有限深度 nested subagent；
- MCP adapter、MCP policy、MCP audit；
- handoff 日志、pipeline decision 日志；
- evidence ledger 和 plan repair 已经在主 workflow 层建立起来。

这说明我们已经不是“只有单 agent loop 的教学 demo”了。现在的问题不是有没有 subagent，而是：

- subagent 还不够像一个真正长期可运行的工程系统；
- orchestration 还比较像“受控 demo”，不是“生产级协作运行时”；
- durability、state machine、conflict isolation、quality gate、evaluation 还不够强。

## 2. 我们和 Claude Code 的差距

### 2.1 Claude 强在哪里

根据 Claude 官方文档，Claude Code 的 subagent 和并行体系已经不只是“子助手”：

- 自定义 subagent 支持独立 context、system prompt、tool access、permission、hooks、skills、memory、model；来源还可以是 managed settings、CLI、项目目录、用户目录、插件目录。见 Claude subagents 文档。  
  来源：<https://code.claude.com/docs/en/sub-agents>
- Claude 已经把 subagent、agent teams、worktrees 这三层并行模型区分开了：
  - subagent：同一 session 内的隔离 worker；
  - agent teams：多独立 session 的协作团队；
  - worktrees：文件系统隔离。  
  来源：<https://code.claude.com/docs/en/sub-agents>  
  来源：<https://code.claude.com/docs/en/agent-teams>  
  来源：<https://code.claude.com/docs/en/worktrees>
- Claude hooks 的事件面比我们更宽，已经覆盖 `PermissionRequest`、`SubagentStop`、`PreCompact` 等生命周期点，而且 `PermissionRequest` 不只是观测，还能直接 allow/deny 和修改输入。  
  来源：<https://code.claude.com/docs/en/hooks>
- Claude 的 worktree 设计已经把“并行不撞文件”这个工程问题认真处理了，甚至支持 subagent 使用 worktree 隔离。  
  来源：<https://code.claude.com/docs/en/worktrees>

### 2.2 我们现在最明显的短板

和 Claude 对比，当前最关键的差距有六个：

1. `Subagent != 独立执行单元`
当前 subagent 已经有私有 prompt、memory、hooks、MCP，但还不是强隔离执行单元。它缺少：
- 文件系统隔离；
- branch/worktree 隔离；
- 更清晰的资源预算；
- 更稳定的可恢复执行状态。

2. `Orchestration 还是“单领导强控制”`
现在 orchestration 主要还是：
- 主 agent 选人；
- 主 agent 安排；
- 子 agent 返回。

它还没到 Claude agent teams 那种：
- 子 agent 之间可以真正协作；
- 有共享任务状态；
- 有自领取任务；
- 有队列和冲突控制。

3. `并行能力偏保守`
目前并行只允许只读 subagent，这个决定是对的，但说明我们还没有解决：
- 写冲突隔离；
- 同 repo 多 worker 的协调；
- merge/rebase/patch conflict 恢复；
- 不同 subagent 对共享上下文的并发一致性。

4. `resume 还是“恢复聊天历史”，不是“恢复工作流状态机”`
现在 resume 更接近：
- 恢复 messages；
- 恢复 tool results。

但工程级 resume 需要：
- 恢复任务图；
- 恢复依赖关系；
- 恢复未完成 step；
- 恢复锁、预算、审批态、待验证态。

5. `质量门控还不够前置`
我们现在有 evidence ledger 和 plan repair，这很好，但更多是“事后可复盘”。
真正工程化还需要：
- 子任务完成前的 quality gate；
- plan approval gate；
- merge gate；
- risk-based reviewer gate；
- 自动回退或 reroute。

6. `缺少更强的观测和评测闭环`
现在我们能看 handoff、pipeline decision、session、benchmark report。
但如果目标是 2.0 的工程级 subagent，仍然缺：
- per-subagent trace；
- DAG 级任务视图；
- delegation success rate；
- rework rate；
- dead-end / loop / stall 指标；
- parallel efficiency 指标；
- 文件冲突率；
- subagent cost / token / latency profile。

## 3. 有没有比 Claude 官方更值得借鉴的设计

结论先说：

- 没有哪个公开方案能“整体上全面碾压 Claude Code”；
- 但在某些单项能力上，确实有比 Claude 官方文档更工程化、更适合我们借鉴的设计。

### 3.1 LangGraph / Deep Agents：在“持久化状态机”上更值得借鉴

LangGraph 官方把重点明确放在：

- durable execution；
- streaming；
- human-in-the-loop；
- persistence；
- comprehensive memory。  
来源：<https://docs.langchain.com/oss/python/langgraph/overview>

这条路线的价值在于：

- Claude 的文档更像“产品能力集合”；
- LangGraph 更像“可恢复状态机 runtime”；
- 如果我们想把 subagent 做成工程系统，而不是只是更像 Claude 的产品表面，那么 LangGraph 这套“图 + 持久化 + 可恢复 + 人工介入”的思路非常值得吸收。

对我们最有价值的不是它的 API 形式，而是它的三个思想：

1. 子任务之间最好是显式图关系，而不是纯 prompt 串联。
2. 状态要能恢复，不是只能重跑。
3. 人工介入点要是 runtime 一等公民，不是外部补丁。

### 3.2 OpenAI Agents SDK：在“handoff/guardrails/session/tracing”上更结构化

OpenAI Agents SDK 官方文档把这些点直接做成一等模块：

- handoffs；
- guardrails；
- sessions；
- tracing。  
来源：<https://openai.github.io/openai-agents-python/>

这对我们有两个启发：

1. `subagent handoff` 不该只是“把 prompt 扔给下一个 agent”
它应该是结构化转交，包含：
- task contract；
- expected output；
- constraints；
- evidence；
- remaining risks；
- budget；
- approval state。

2. `guardrail` 应该和 orchestration 同级，而不是附属规则
也就是：
- planner guardrail；
- delegation guardrail；
- tool guardrail；
- merge guardrail；
- verification guardrail；
- shutdown guardrail。

如果我们把这些做强，在治理层面会比 Claude 官方文档里更清晰。

### 3.3 Temporal：在“可恢复执行”和“事件历史”上明显更强

Temporal 官方文档最值得借鉴的点不是 agent，而是 workflow runtime：

- workflow resilient；
- event history 是 source of truth；
- crash 后能按历史恢复状态；
- activity 结果被记录，replay 时不会重复外部副作用。  
来源：<https://docs.temporal.io/workflows>

这个思路对 subagent 2.0 很关键。

Claude 文档现在更多强调：
- 可以并行；
- 可以 worktree；
- 可以团队协作。

但如果我们真想做工程级 subagent，我认为 Temporal 这条思路在“底层执行模型”上比 Claude 更值得学：

- 不要只存 transcript；
- 要存 event history；
- 不要只会 resume conversation；
- 要能 replay workflow；
- 不要让失败后只能从头来；
- 要能从上一个稳定事件继续。

### 3.4 Claude 官方仍然领先的地方

也不能误判。Claude 官方仍然在这些方向上更完整：

- 产品化整合：subagent、teams、worktrees、hooks、skills、plugins、desktop/web/terminal 一体化；
- 配置作用域：managed / user / project / plugin 非常清晰；
- coding 场景贴合度高；
- 真正面向“开发者日常使用”而不是单独 runtime 研究。

所以我们的策略不应该是“复制 Claude”或“抛弃 Claude”，而应该是：

- 产品形态参考 Claude；
- runtime 设计吸收 LangGraph / OpenAI Agents SDK / Temporal 的强项。

## 4. 到 2.0 的核心目标

### 4.1 2.0 的一句话目标

到 `2.0` 时，subagent 不只是“被调用的助手”，而应该成为：

`可调度、可恢复、可审计、可隔离、可验证、可评测的工程级协作执行单元`

### 4.2 2.0 的完成标准

我建议用下面这 10 条作为 2.0 完成标准：

1. Subagent 支持显式 task contract。
2. Subagent 支持 worktree / branch 级文件隔离。
3. 并行写任务可安全运行，不再只限只读并行。
4. Resume 恢复的是 workflow state，不只是聊天历史。
5. 有明确的 plan approval / quality gate / merge gate。
6. 有 per-subagent token、latency、success、retry、stall 指标。
7. 有 event-history 级可回放日志。
8. 有 subagent DAG / task graph 视图。
9. 有 benchmark 自动化评测覆盖 subagent orchestration。
10. 在一些底层运行时指标上，不弱于 Claude，并在 durability / traceability 上争取更强。

## 5. 版本路线图：从现在到 2.0

下面假设我们从下一版开始按十进制写成：

- `1.2`
- `1.3`
- ...
- `2.0`

### 5.1 `1.2` Subagent Task Contract

目标：

- 每次 delegation 不再只是自然语言 prompt；
- 引入结构化 `task_contract`。

要做的事：

- 定义 contract schema：
  - objective；
  - deliverable；
  - constraints；
  - allowed_tools；
  - expected_evidence；
  - budget；
  - stop_conditions；
- handoff 统一写入 contract；
- session / handoff log / pipeline decision 都引用同一个 contract id。

价值：

- 降低 subagent 任务漂移；
- 为后续 resume、quality gate、evaluation 打地基。

### 5.2 `1.3` Subagent State Machine v1

目标：

- 从“调用式 worker”升级成“有生命周期状态的执行单元”。

要做的事：

- 定义状态：
  - planned；
  - ready；
  - running；
  - blocked；
  - waiting_approval；
  - verifying；
  - completed；
  - failed；
  - abandoned；
- 每个 subagent 都有状态流转事件；
- 明确谁能触发状态变化。

价值：

- 后面 resume、调度、重试、审批才有可靠基础。

### 5.3 `1.4` Subagent Event History and Replay

目标：

- transcript 之外，建立 workflow event history。

要做的事：

- 每次 delegation、tool call、approval、retry、verification、handoff 都写 event；
- event 成为 state 重建依据；
- 支持 replay 到最近稳定点。

主要借鉴：

- Temporal 的 event history / replay 思路。  
  来源：<https://docs.temporal.io/workflows>

### 5.4 `1.5` Worktree-Isolated Subagents

目标：

- 让写型 subagent 拥有真正文件隔离。

要做的事：

- 每个 write-capable subagent 可选独立 worktree；
- 配置 `baseRef` 策略；
- worktree 生命周期清理；
- `.worktreeinclude` 风格的本地文件复制策略；
- parent/child worktree 关系记录。

主要借鉴：

- Claude 的 worktree 模型。  
  来源：<https://code.claude.com/docs/en/worktrees>

### 5.5 `1.6` Parallel Write Subagents

目标：

- 不再只有只读并行；
- 支持“隔离写 + 后续汇合”的并行写 worker。

要做的事：

- worktree 内并行写；
- 输出统一变成 patch / diff / commit candidate；
- 引入 conflict detector；
- 禁止多个 writer 直接写同一工作目录。

这是一个关键分水岭。

如果这一版做不好，subagent 永远只能停留在“研究员”，不能成为“真正的工程协作者”。

### 5.6 `1.7` Approval and Quality Gates

目标：

- 在 subagent 完成前增加前置门控。

要做的事：

- plan approval gate；
- implementation gate；
- verification gate；
- merge gate；
- risk-specific reviewer gate；
- hook 可阻断 task completion。

主要借鉴：

- Claude agent teams 的 teammate plan approval；
- OpenAI Agents SDK 的 guardrails 思路。  
  来源：<https://code.claude.com/docs/en/agent-teams>  
  来源：<https://openai.github.io/openai-agents-python/>

### 5.7 `1.8` Shared Task Graph

目标：

- orchestration 从 pipeline 变成 task graph。

要做的事：

- DAG 形式任务依赖；
- task claim / release / retry；
- blocked-on 关系；
- self-claim 和 lead-assignment 两种模式；
- file lock / task lock。

主要借鉴：

- Claude agent teams 的 shared task list；
- LangGraph 的图执行思路。  
  来源：<https://code.claude.com/docs/en/agent-teams>  
  来源：<https://docs.langchain.com/oss/python/langgraph/overview>

### 5.8 `1.9` Teammate Communication and Negotiation

目标：

- 子 agent 不再只能“向上汇报”，而能有限度横向通信。

要做的事：

- teammate message channel；
- structured question / answer / artifact exchange；
- negotiation hooks；
- contradiction detection；
- critic agent 可驳回 implementer 产出。

这一版完成后，subagent 才开始接近 Claude agent teams。

### 5.9 `2.0` Subagent Runtime v2

目标：

- 完成工程级 subagent runtime 收口。

2.0 必须同时具备：

- task contract；
- lifecycle state machine；
- event history + replay；
- worktree-isolated writers；
- safe parallel write orchestration；
- approval / quality / merge gates；
- task graph；
- teammate communication；
- trace / metrics / evaluation；
- benchmark 级自动回归。

## 6. 我建议的优先级排序

如果目标是“2.0 时 subagent 到工程级甚至更优”，最优顺序不是按功能表面排，而是按底层依赖排：

1. `1.2` Task Contract
2. `1.3` State Machine
3. `1.4` Event History and Replay
4. `1.5` Worktree Isolation
5. `1.6` Parallel Write Subagents
6. `1.7` Approval and Quality Gates
7. `1.8` Shared Task Graph
8. `1.9` Teammate Communication
9. `2.0` Runtime v2 收口

原因很简单：

- 没有 contract，handoff 会漂；
- 没有状态机，resume 会乱；
- 没有 event history，replay 只是幻想；
- 没有 worktree，写并行就是事故；
- 没有 gate，团队协作质量不可控；
- 没有 task graph，横向协作无法工程化。

## 7. 我对“比 Claude 更好”的实际判断

如果我们只比“功能数量”，短期内很难超过 Claude。

但如果目标改成“底层工程质量”，我们有三条可以做到比 Claude 官方文档更强的路线：

### 7.1 在 durability 上更强

做法：

- 引入 event-history + replay；
- subagent resume 从 conversation 恢复升级成 workflow 恢复；
- 明确稳定点和恢复点。

这一点可以向 Temporal 靠拢，而不仅仅向 Claude 靠拢。

### 7.2 在 observability 上更强

做法：

- trace id；
- handoff id；
- task contract id；
- per-subagent metrics；
- plan deviation taxonomy；
- benchmark 到 orchestration 的统一报表。

Claude 产品化很强，但公开文档里不强调这些 runtime 内核指标。这里反而是我们可以做得更“工程”的地方。

### 7.3 在治理结构上更强

做法：

- contract guardrail；
- delegation guardrail；
- approval gate；
- merge gate；
- MCP trust profile；
- risk-scoped worktree policy；
- artifact-level verification。

如果这些做好，我们在“可控自治”这一点上可能比 Claude 官方公开路线更清晰。

## 8. 建议的近期落地动作

如果现在立刻开做，我建议第一步不是直接碰 `2.0`，而是先开 `1.2`：

- 建 `task_contract` schema；
- 改 handoff / pipeline decision / session event；
- 给 `subagent_run` 和 `subagent_pipeline` 都接上 contract id；
- 加最小测试集：
  - contract validation；
  - missing field fallback；
  - nested handoff contract inheritance；
  - benchmark report 中增加 subagent contract diagnostics。

这样做的原因是：

- `1.2` 会成为 `1.3` 到 `2.0` 的共同基础；
- 一旦 contract 稳了，后面的 state machine、replay、gate、graph 都有统一接口；
- 如果一开始不做这层，后面每一版都会返工。

## 9. 参考资料

- Claude Code subagents: <https://code.claude.com/docs/en/sub-agents>
- Claude Code hooks: <https://code.claude.com/docs/en/hooks>
- Claude Code agent teams: <https://code.claude.com/docs/en/agent-teams>
- Claude Code worktrees: <https://code.claude.com/docs/en/worktrees>
- Claude Code MCP: <https://code.claude.com/docs/en/mcp>
- LangGraph overview: <https://docs.langchain.com/oss/python/langgraph/overview>
- OpenAI Agents SDK: <https://openai.github.io/openai-agents-python/>
- Temporal workflows: <https://docs.temporal.io/workflows>

## 10. 最后结论

当前我们的 subagent 已经跨过了“有没有”的阶段，进入了“如何工程化”的阶段。

和 Claude 相比，我们现在主要输在：

- 并行写隔离；
- 团队协作；
- 状态恢复；
- 任务图；
- 质量门控；
- 运行时观测。

但如果路线设计得对，我们完全可以在 `2.0` 之前走出一条不只是“像 Claude”，而是“底层执行模型比公开 Claude 路线更工程化”的分支：

- 产品形态向 Claude 靠；
- 状态机和恢复向 Temporal 靠；
- orchestration runtime 向 LangGraph 靠；
- handoff / guardrail / tracing 向 OpenAI Agents SDK 靠。

如果只选一个马上开始的版本，建议就是 `1.2 Subagent Task Contract`。

# 生产可用性评估

本文档说明 Mini Claude Code 当前距离“可生产交付”的状态，以及已经补齐的工程化能力。

## 当前结论

当前项目已经从“教学原型”升级到“可演示、可安装、可测试、可审计”的工程样品。

它适合：

- 甲方验收 Demo；
- HR / 面试官初筛；
- AI Agent 架构展示；
- 本地 mock 演示；
- 工具调用、权限、hooks、MCP、subagent 的工程能力说明。

它还不等同于商业级 Claude Code 替代品。真实生产部署前仍建议继续补充 installer、签名、崩溃上报、在线更新、真实用户权限确认 UI 和更完整的端到端评测。

## 已补齐的生产化基础

### 安装与启动

- `pyproject.toml`：项目可以通过 `pip install -e .` 安装；
- `mini-cc` 命令入口：启动 CLI；
- `mini-cc-desktop` 命令入口：启动桌面软件；
- `scripts/start_desktop.bat`：Windows 双击启动；
- `scripts/start_desktop.ps1`：PowerShell 启动，自动查找 Python；
- `requirements.txt`：保留简单依赖安装入口。

### 自动化验证

- `.github/workflows/ci.yml`：GitHub Actions 自动运行；
- CI 覆盖 Python 3.10、3.11、3.12；
- CI 安装包后运行 `python -m unittest discover`；
- CI 运行 mock agent smoke，证明 CLI 不只是能 import。

### 最新本地验证结果

本轮生产可用性加固后，已在 Windows 本机完成以下验证：

- `python -m unittest discover`：220 个单元测试通过；
- `scripts/health_check.ps1 -Full`：通过；
- mock agent smoke：通过；
- runtime report smoke：通过；
- `.mini_cc`、运行日志、`.env` 忽略检查：通过；
- 敏感信息扫描：未发现 API key、私有 endpoint 或本机绝对路径进入公开文档。

同时修复了一个 subagent 隔离写入问题：当 workspace 只是外层 Git 仓库里的临时目录时，不再误把外层仓库当成当前项目的 git 基线，避免 diff 和合并检查混入整套项目文件。

### 本地健康检查

`scripts/health_check.ps1` 会检查：

- Python 版本；
- `mini_cc` 导入；
- 桌面应用语法编译；
- mock agent smoke；
- runtime report smoke；
- `.mini_cc`、日志和 `.env` 是否被 Git 忽略；
- 可选全量单元测试。

运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/health_check.ps1
```

运行完整测试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/health_check.ps1 -Full
```

### 安全默认值

- `.mini_cc/` 已加入 `.gitignore`；
- `.env` 已加入 `.gitignore`；
- 桌面设置中的 API key 只保存在本地；
- 日志和运行产物默认不提交；
- 权限系统区分 read、verify、workspace_write、network、package_manager、docker、destructive 等风险；
- `git clone` 已归类为 network，不再误判成 unknown shell。

### 证据与可追踪性

- runtime report；
- tool-use eval；
- permission ledger；
- evidence ledger；
- hook event log；
- subagent task graph；
- Terminal-Bench smoke runner；
- 版本迭代记录 `VERSION_HISTORY.md`。

## 仍然不是生产级的部分

### 桌面分发

当前仍依赖本机 Python 环境。更像工程 Demo，不是最终用户安装包。

建议后续补：

- PyInstaller / Briefcase / Nuitka 打包；
- Windows installer；
- 代码签名；
- GitHub Release 自动上传构建产物。

### 真实模型稳定性

真实效果取决于用户配置的模型、API endpoint、网络状态和上下文长度。

建议后续补：

- 请求重试和限流；
- 模型错误分类；
- API endpoint 健康探测；
- 更明确的 UI 错误提示；
- token 使用量和费用统计。

### 权限交互

目前权限系统在 runtime 层较完整，但桌面 UI 还没有做到商业产品级的逐项确认弹窗。

建议后续补：

- 写文件前确认；
- shell 命令前确认；
- 网络访问前确认；
- 命令白名单 / 黑名单 UI；
- workspace 边界可视化。

### 可观测性

已有日志和 report，但还没有完整产品级 telemetry。

建议后续补：

- 崩溃报告；
- UI 侧错误收集；
- 长任务进度条；
- trace viewer；
- 导出诊断包。

### 外部评测

已有 tool-use eval、runtime report、Terminal-Bench smoke。完整 Terminal-Bench / SWE-bench 仍需要 Docker、磁盘空间和较长时间。

建议后续补：

- 固定 benchmark 子集；
- 公开 benchmark artifact；
- 每次 release 生成评测报告；
- GitHub Actions artifact 上传。

## 甲方验收建议

建议验收时按以下顺序执行：

1. 打开 GitHub README，确认截图、中文文档、启动方式、测试方式齐全；
2. 运行 `scripts/start_desktop.bat`，确认桌面软件可打开；
3. 用 mock 模式跑一个任务，确认无需 API key 也能演示；
4. 运行 `scripts/health_check.ps1`；
5. 运行 `python -m unittest discover`；
6. 查看 `docs/architecture.md` 和本文件；
7. 查看 `VERSION_HISTORY.md`，确认项目不是一次性空壳。

## 生产可用性评分

按当前状态估算：

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| 架构完整度 | 高 | Agent runtime 模块较完整 |
| 可演示性 | 高 | 桌面 UI、截图、mock、脚本齐全 |
| 可测试性 | 高 | 220 个单元测试和 CI |
| 安全默认值 | 中高 | 本地密钥排除、权限分类、ledger |
| 可安装性 | 中 | 支持 pip editable，尚无 installer |
| 可运维性 | 中 | 有 report / health check，缺崩溃上报 |
| 商业产品体验 | 中 | Tkinter UI 可用，但视觉和交互仍可升级 |

总体判断：当前适合作为“工程化 AI Agent Demo / MVP”，还不是面向普通终端用户的商业级桌面产品。

const fields = {
  provider: document.getElementById("provider"),
  apiKey: document.getElementById("apiKey"),
  baseUrl: document.getElementById("baseUrl"),
  model: document.getElementById("model"),
  reasoningEffort: document.getElementById("reasoningEffort"),
  workspace: document.getElementById("workspace"),
  permissionMode: document.getElementById("permissionMode"),
  maxTurns: document.getElementById("maxTurns"),
  timeout: document.getElementById("timeout"),
  s20: document.getElementById("s20"),
  prompt: document.getElementById("prompt"),
};

const ui = {
  runBtn: document.getElementById("runBtn"),
  clearBtn: document.getElementById("clearBtn"),
  output: document.getElementById("output"),
  statusPill: document.getElementById("statusPill"),
  backendStatus: document.getElementById("backendStatus"),
  workspaceStatus: document.getElementById("workspaceStatus"),
  runStatus: document.getElementById("runStatus"),
  versionText: document.getElementById("versionText"),
  currentPageTitle: document.getElementById("currentPageTitle"),
  currentGoal: document.getElementById("currentGoal"),
  progressBar: document.getElementById("progressBar"),
  toolCalls: document.getElementById("toolCalls"),
  memoryState: document.getElementById("memoryState"),
  guardrailsList: document.getElementById("guardrailsList"),
  finalAnswer: document.getElementById("finalAnswer"),
  answerBadge: document.getElementById("answerBadge"),
  tokenMetric: document.getElementById("tokenMetric"),
  latencyMetric: document.getElementById("latencyMetric"),
  toolMetric: document.getElementById("toolMetric"),
  modelBadge: document.getElementById("modelBadge"),
  toolsBadge: document.getElementById("toolsBadge"),
  workflowBadge: document.getElementById("workflowBadge"),
  runtimeBadge: document.getElementById("runtimeBadge"),
  providerSummary: document.getElementById("providerSummary"),
  permissionSummary: document.getElementById("permissionSummary"),
  turnSummary: document.getElementById("turnSummary"),
  timeoutSummary: document.getElementById("timeoutSummary"),
  themeToggle: document.getElementById("themeToggle"),
};

const STORAGE_KEY = "mini_cc_frontend_config_v2";
const THEME_KEY = "mini_cc_frontend_theme";

const loopSteps = [
  "User Input",
  "Planning",
  "Tool Use",
  "Observation",
  "Memory Update",
  "Final Response",
];

const guardrailConfig = [
  ["permission", "Permission policy", "运行前按 ask / auto / read-only 判断风险。"],
  ["secret", "Secret redaction", "API key 只进入子进程环境变量，输出会脱敏。"],
  ["workspace", "Workspace boundary", "工具默认在配置的 workspace 内执行。"],
  ["timeout", "Runtime timeout", "前端请求会按 timeout 保护运行。"],
];

const mockMemory = [
  ["user_instructions", "用户明确指令", "最高优先级"],
  ["recent_session_facts", "最近会话事实", "运行后更新"],
  ["tool_summaries", "工具摘要", "由 trace 提取"],
  ["compressed_conversation", "压缩对话", "按预算保留"],
];

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function loadSavedConfig() {
  const raw = localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    for (const [key, field] of Object.entries(fields)) {
      if (key === "apiKey" || key === "prompt") continue;
      if (!(key in saved)) continue;
      if (field.type === "checkbox") {
        field.checked = Boolean(saved[key]);
      } else {
        field.value = saved[key];
      }
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
  }
}

function saveConfig() {
  const saved = {};
  for (const [key, field] of Object.entries(fields)) {
    if (key === "apiKey" || key === "prompt") continue;
    saved[key] = field.type === "checkbox" ? field.checked : field.value;
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  updateSummaries();
}

function payloadFromForm() {
  return {
    provider: fields.provider.value,
    apiKey: fields.apiKey.value,
    baseUrl: fields.baseUrl.value,
    model: fields.model.value,
    reasoningEffort: fields.reasoningEffort.value,
    workspace: fields.workspace.value,
    permissionMode: fields.permissionMode.value,
    maxTurns: Number(fields.maxTurns.value || 8),
    timeout: Number(fields.timeout.value || 120),
    s20: fields.s20.checked,
    prompt: fields.prompt.value,
  };
}

function setStatus(text, mode = "idle") {
  const dot = mode === "running" ? "running" : mode === "ok" ? "ok" : mode === "error" ? "error" : "idle";
  ui.statusPill.innerHTML = `<span class="dot ${dot}"></span>${escapeHtml(text)}`;
  ui.statusPill.className = `status-pill ${mode}`.trim();
  ui.runStatus.textContent = text;
  ui.runStatus.className = `badge ${mode === "ok" ? "ok" : mode === "error" ? "danger" : "neutral"}`;
  ui.runtimeBadge.textContent = text;
  ui.runtimeBadge.className = `badge ${mode === "ok" ? "ok" : mode === "error" ? "danger" : "neutral"}`;
}

function setProgress(percent) {
  ui.progressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function renderLoop(activeIndex = -1) {
  const html = loopSteps
    .map((step, index) => {
      const active = index <= activeIndex ? "active" : "";
      const arrow = index === loopSteps.length - 1 ? "" : '<span class="flow-arrow">→</span>';
      return `<div class="flow-node ${active}">${escapeHtml(step)}</div>${arrow}`;
    })
    .join("");
  document.getElementById("loopFlow").innerHTML = html;
}

function renderGuardrails(mode = "idle") {
  ui.guardrailsList.innerHTML = guardrailConfig
    .map(([id, title, text]) => {
      const status = mode === "error" && id === "timeout" ? "warning" : "ok";
      const label = status === "ok" ? "pass" : "check";
      return `
        <article class="list-item">
          <div>
            <strong>${escapeHtml(title)}</strong>
            <p>${escapeHtml(text)}</p>
          </div>
          <span class="badge ${status === "ok" ? "ok" : "warning"}">${label}</span>
        </article>`;
    })
    .join("");
}

function renderMemoryState(extra = {}) {
  const rows = mockMemory.map(([key, label, value]) => [key, label, extra[key] || value]);
  ui.memoryState.innerHTML = rows
    .map(
      ([key, label, value]) => `
        <div class="state-item">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(key)}</small>
        </div>`
    )
    .join("");
}

function emptyToolCalls() {
  ui.toolCalls.innerHTML = `<div class="empty-state compact">还没有工具调用。运行任务后会展示工具名、输入摘要、状态和耗时。</div>`;
  ui.toolsBadge.textContent = "待调用";
  ui.toolMetric.textContent = "0";
}

function parseTrace(data) {
  const trace = data?.result?.trace;
  if (Array.isArray(trace)) return trace.map(String);
  const stdout = data?.stdout ? String(data.stdout).split(/\r?\n/) : [];
  return stdout.filter(Boolean);
}

function parseToolCalls(traceLines) {
  const calls = [];
  for (const line of traceLines) {
    if (line.startsWith("[tool] ")) {
      const match = line.match(/^\[tool\]\s+([^(]+)\((.*)\)$/);
      calls.push({
        name: match ? match[1].trim() : "tool",
        input: match ? match[2].trim() : line.replace("[tool]", "").trim(),
        status: "running",
        output: "",
      });
    } else if (line.startsWith("[tool ok]") || line.startsWith("[tool error]")) {
      const latest = calls[calls.length - 1];
      if (latest) {
        latest.status = line.startsWith("[tool ok]") ? "ok" : "error";
        latest.output = line.replace(/^\[tool (ok|error)\]\s*/, "").trim();
      }
    }
  }
  return calls;
}

function renderToolCalls(calls) {
  if (!calls.length) {
    emptyToolCalls();
    return;
  }
  ui.toolsBadge.textContent = `${calls.length} calls`;
  ui.toolsBadge.className = "badge ok";
  ui.toolMetric.textContent = String(calls.length);
  ui.toolCalls.innerHTML = calls
    .map((call, index) => {
      const badgeClass = call.status === "ok" ? "ok" : call.status === "error" ? "danger" : "neutral";
      return `
        <details class="tool-call" ${index === calls.length - 1 ? "open" : ""}>
          <summary>
            <span>${escapeHtml(call.name)}</span>
            <span class="badge ${badgeClass}">${escapeHtml(call.status)}</span>
          </summary>
          <div class="tool-body">
            <p><strong>Input</strong></p>
            <code>${escapeHtml(call.input || "{}")}</code>
            <p><strong>Output</strong></p>
            <code>${escapeHtml(call.output || "等待工具结果")}</code>
          </div>
        </details>`;
    })
    .join("");
}

function extractFinalAnswer(data) {
  const trace = parseTrace(data);
  const textLines = trace.filter((line) => {
    const trimmed = line.trim();
    return (
      trimmed &&
      !trimmed.startsWith("[tool]") &&
      !trimmed.startsWith("[tool ok]") &&
      !trimmed.startsWith("[tool error]") &&
      !trimmed.startsWith("Stopped after max_turns=")
    );
  });
  if (textLines.length) return textLines[textLines.length - 1];
  if (data.stdout) return String(data.stdout).trim();
  if (data.error) return data.error;
  return "没有可展示的最终回答。";
}

function formatRunResult(data) {
  const lines = [];
  lines.push(`ok: ${data.ok}`);
  if (data.returncode !== undefined) lines.push(`returncode: ${data.returncode}`);
  if (data.workspace) lines.push(`workspace: ${data.workspace}`);
  if (data.command) lines.push(`command: ${data.command}`);
  if (data.error) lines.push(`error: ${data.error}`);
  if (data.result) {
    lines.push("");
    lines.push("result:");
    lines.push(JSON.stringify(data.result, null, 2));
  }
  if (data.stdout && !data.result) {
    lines.push("");
    lines.push("stdout:");
    lines.push(data.stdout);
  }
  if (data.stderr) {
    lines.push("");
    lines.push("stderr:");
    lines.push(data.stderr);
  }
  return lines.join("\n");
}

function updateSummaries() {
  ui.providerSummary.textContent = fields.provider.value;
  ui.permissionSummary.textContent = fields.permissionMode.value;
  ui.turnSummary.textContent = fields.maxTurns.value || "8";
  ui.timeoutSummary.textContent = `${fields.timeout.value || "120"}s`;
  const modelName = fields.model.value || (fields.provider.value === "mock" ? "MockProvider" : "未填写模型");
  ui.modelBadge.textContent = modelName;
  ui.modelBadge.className = fields.provider.value === "mock" ? "badge neutral" : "badge ok";
}

function validatePayload(payload) {
  if (!payload.prompt.trim()) return "请先输入任务目标。";
  if (payload.provider !== "mock" && !payload.apiKey.trim()) return "非 Mock 模式需要 API key。";
  if (payload.maxTurns < 1 || payload.maxTurns > 30) return "Max turns 需要在 1 到 30 之间。";
  if (payload.timeout < 5 || payload.timeout > 600) return "Timeout 需要在 5 到 600 秒之间。";
  return "";
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "status failed");
    ui.backendStatus.textContent = "已连接";
    ui.versionText.textContent = `v${data.version}`;
    if (!fields.workspace.value) fields.workspace.value = data.defaultWorkspace;
    ui.workspaceStatus.textContent = fields.workspace.value;
    setStatus("Ready", "idle");
    saveConfig();
  } catch (error) {
    ui.backendStatus.textContent = "未连接";
    ui.output.textContent = `无法连接本地后端：${error.message}`;
    setStatus("后端未连接", "error");
  }
}

async function runAgent() {
  const payload = payloadFromForm();
  const validationError = validatePayload(payload);
  if (validationError) {
    ui.output.textContent = validationError;
    ui.finalAnswer.textContent = validationError;
    ui.finalAnswer.className = "empty-state error-state";
    setStatus("校验失败", "error");
    return;
  }

  saveConfig();
  const startedAt = performance.now();
  ui.runBtn.disabled = true;
  fields.prompt.disabled = true;
  setStatus("运行中", "running");
  setProgress(35);
  renderLoop(2);
  ui.currentGoal.textContent = payload.prompt.trim().slice(0, 120);
  ui.workspaceStatus.textContent = payload.workspace;
  ui.output.textContent = "正在运行 agent...";
  ui.finalAnswer.textContent = "Agent 正在规划、调用工具或生成最终回答。";
  ui.finalAnswer.className = "empty-state";
  ui.answerBadge.textContent = "运行中";
  ui.answerBadge.className = "badge neutral";
  ui.workflowBadge.textContent = "Running";
  ui.workflowBadge.className = "badge neutral";
  emptyToolCalls();

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    const elapsedMs = Math.round(performance.now() - startedAt);
    ui.output.textContent = formatRunResult(data);
    ui.latencyMetric.textContent = `${elapsedMs}ms`;
    ui.tokenMetric.textContent = estimateTokens(JSON.stringify(data));

    const trace = parseTrace(data);
    const calls = parseToolCalls(trace);
    renderToolCalls(calls);
    renderMemoryState({
      tool_summaries: `${calls.length} tool calls`,
      recent_session_facts: response.ok && data.ok ? "completed run" : "failed run",
    });

    const finalAnswer = extractFinalAnswer(data);
    ui.finalAnswer.textContent = finalAnswer;
    ui.finalAnswer.className = "answer-box";
    ui.answerBadge.textContent = response.ok && data.ok ? "Completed" : "Needs review";
    ui.answerBadge.className = response.ok && data.ok ? "badge ok" : "badge danger";
    renderLoop(response.ok && data.ok ? 5 : 3);
    setProgress(response.ok && data.ok ? 100 : 72);
    renderGuardrails(response.ok && data.ok ? "ok" : "error");

    if (response.ok && data.ok) {
      setStatus("完成", "ok");
      ui.workflowBadge.textContent = "Completed";
      ui.workflowBadge.className = "badge ok";
    } else {
      setStatus("失败", "error");
      ui.workflowBadge.textContent = "Error";
      ui.workflowBadge.className = "badge danger";
    }
  } catch (error) {
    const elapsedMs = Math.round(performance.now() - startedAt);
    ui.output.textContent = `请求失败：${error.message}`;
    ui.finalAnswer.textContent = `请求失败：${error.message}`;
    ui.finalAnswer.className = "empty-state error-state";
    ui.latencyMetric.textContent = `${elapsedMs}ms`;
    setStatus("失败", "error");
    setProgress(72);
    renderLoop(2);
    renderGuardrails("error");
  } finally {
    ui.runBtn.disabled = false;
    fields.prompt.disabled = false;
  }
}

function estimateTokens(text) {
  if (!text) return "-";
  return `~${Math.max(1, Math.ceil(text.length / 4))}`;
}

function setProviderDefaults() {
  if (fields.provider.value === "mock") {
    fields.model.placeholder = "mock 不需要 model";
    fields.apiKey.placeholder = "mock 不需要 API key";
  } else if (fields.provider.value === "openai") {
    fields.model.placeholder = "gpt-5.5";
    fields.apiKey.placeholder = "输入 OpenAI-compatible API key";
  } else {
    fields.model.placeholder = "claude-sonnet-4-6";
    fields.apiKey.placeholder = "输入 Anthropic-compatible API key";
  }
  updateSummaries();
}

function bindNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const target = document.getElementById(button.dataset.section);
      ui.currentPageTitle.textContent = button.textContent.trim();
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function bindPanelTargets() {
  document.querySelectorAll("[data-panel-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.panelTarget);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
}

function bindThemeToggle() {
  const saved = localStorage.getItem(THEME_KEY) || "light";
  applyTheme(saved);
  ui.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
}

ui.clearBtn.addEventListener("click", () => {
  if (!confirm("确定清空当前日志显示吗？这不会删除后端文件。")) return;
  ui.output.textContent = "等待运行。";
  setStatus("Ready", "idle");
  setProgress(0);
  ui.finalAnswer.textContent = "运行完成后，这里会展示模型最终回答；原始 stdout / stderr 保留在右侧日志。";
  ui.finalAnswer.className = "empty-state";
  ui.answerBadge.textContent = "等待运行";
  ui.answerBadge.className = "badge neutral";
  emptyToolCalls();
});

ui.runBtn.addEventListener("click", runAgent);
fields.provider.addEventListener("change", () => {
  setProviderDefaults();
  saveConfig();
});

for (const field of Object.values(fields)) {
  field.addEventListener("change", saveConfig);
  field.addEventListener("input", () => {
    if (field === fields.prompt) return;
    updateSummaries();
  });
}

loadSavedConfig();
setProviderDefaults();
bindNavigation();
bindPanelTargets();
bindThemeToggle();
renderLoop(-1);
renderGuardrails("idle");
renderMemoryState();
emptyToolCalls();
updateSummaries();
loadStatus();

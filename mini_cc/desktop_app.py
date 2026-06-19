from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from . import __version__

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
    TkBase = TkinterDnD.Tk
except Exception:
    DND_FILES = "DND_Files"
    DND_AVAILABLE = False
    TkBase = tk.Tk

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO_ROOT / ".mini_cc" / "desktop-settings.json"
RUN_LOG_PATH = REPO_ROOT / ".mini_cc" / "desktop-run.log"
DESKTOP_SESSIONS_PATH = REPO_ROOT / ".mini_cc" / "desktop-sessions.json"
COVER_IMAGE_PATH = REPO_ROOT / "mini_cc" / "assets" / "app_cover.png"
CHILD_PYTHON = Path(sys.executable).with_name("python.exe") if Path(sys.executable).name.lower() == "pythonw.exe" else Path(sys.executable)
WECHAT_GREEN = "#2563eb"
WECHAT_DARK = "#0f172a"
ACCENT_BLUE = "#2563eb"
APP_BG = "#f5f7fb"
PANEL_BG = "#ffffff"
CHAT_BG = "#f7f8fb"
CARD_BG = "#ffffff"
LINE_BG = "#e7ecf2"
HOVER_BG = "#eff5ff"
SOFT_BG = "#f8fafc"
TEXT_MAIN = "#17202a"
TEXT_MUTED = "#667085"
TEXT_FAINT = "#98a2b3"
PROMPT_PLACEHOLDER = "输入任务，或拖入文件 / 图片"


class MiniCCDesktopApp(TkBase):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mini Claude Code")
        self.geometry("1180x820")
        self.minsize(900, 660)
        self.configure(bg=APP_BG)
        self.result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.running = False
        self.current_process: subprocess.Popen[str] | None = None
        self.cover_image: tk.PhotoImage | None = None
        self.cover_thumb: tk.PhotoImage | None = None
        self.live_trace: tk.Text | None = None
        self.run_started_at: float | None = None
        self.tool_call_count = 0
        self.tool_call_rows: list[str] = []
        self.agent_panel_vars: dict[str, tk.StringVar] = {}
        self.tool_call_list: tk.Listbox | None = None
        self.pending_attachments: list[str] = []
        self.attachment_tray: tk.Frame | None = None
        self.attachment_chips: tk.Frame | None = None
        saved = self.load_settings()
        self.sessions = self.load_desktop_sessions()
        self.current_session_id = str(saved.get("desktop_session_id") or "")
        if self.current_session_id not in self.sessions:
            self.current_session_id = self.ensure_desktop_session()
        self.session_listbox: tk.Listbox | None = None
        self.session_list_frame: tk.Frame | None = None
        self._refreshing_sessions = False
        saved_budget = str(saved.get("runtime_budget") or "unlimited")
        if saved_budget == "auto":
            saved_budget = "unlimited"
        self.vars = {
            "provider": tk.StringVar(value=str(saved.get("provider") or "mock")),
            "api_key": tk.StringVar(value=str(saved.get("api_key") or "")),
            "base_url": tk.StringVar(value=str(saved.get("base_url") or "")),
            "model": tk.StringVar(value=str(saved.get("model") or "")),
            "reasoning": tk.StringVar(value=str(saved.get("reasoning") or "")),
            "workspace": tk.StringVar(value=str(saved.get("workspace") or REPO_ROOT)),
            "permission": tk.StringVar(value=str(saved.get("permission") or "auto")),
            "runtime_budget": tk.StringVar(value=saved_budget),
            "s20": tk.BooleanVar(value=bool(saved.get("s20", True))),
            "show_trace": tk.BooleanVar(value=bool(saved.get("show_trace", False))),
            "status": tk.StringVar(value=f"就绪  v{__version__}"),
        }
        self.agent_panel_vars = {
            "state": tk.StringVar(value="Idle"),
            "goal": tk.StringVar(value="Waiting for task"),
            "provider": tk.StringVar(value=self.vars["provider"].get()),
            "tools": tk.StringVar(value="0 calls"),
            "latency": tk.StringVar(value="-"),
            "guardrails": tk.StringVar(value="Permission / secrets / workspace boundary"),
        }
        self.chat_title_var = tk.StringVar(value=self.current_chat_title())
        draft_prompt = str(saved.get("draft_prompt") or "")
        self.initial_prompt = "" if draft_prompt.strip() == "s20 snapshot" else draft_prompt
        self.prompt_placeholder_active = False
        self.build_styles()
        self.install_app_cover()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log_event("desktop_app_started")
        self.render_current_session()
        if not self.current_session().get("messages"):
            self.add_message("agent", "欢迎使用 Mini Claude Code。左侧选择模型连接，底部输入任务后点击发送。")
        self.after(120, self.poll_result_queue)

    def load_settings(self) -> dict[str, Any]:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_settings(self) -> None:
        payload: dict[str, Any] = {
            "provider": self.vars["provider"].get(),
            "api_key": self.vars["api_key"].get(),
            "base_url": self.vars["base_url"].get(),
            "model": self.vars["model"].get(),
            "reasoning": self.vars["reasoning"].get(),
            "workspace": self.vars["workspace"].get(),
            "permission": self.vars["permission"].get(),
            "runtime_budget": self.vars["runtime_budget"].get(),
            "s20": self.vars["s20"].get(),
            "show_trace": self.vars["show_trace"].get(),
            "draft_prompt": self.prompt_value() if hasattr(self, "prompt_text") else "",
            "desktop_session_id": self.current_session_id,
        }
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_desktop_sessions(self) -> dict[str, dict[str, Any]]:
        if not DESKTOP_SESSIONS_PATH.exists():
            return {}
        try:
            payload = json.loads(DESKTOP_SESSIONS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in sessions:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                item.setdefault("messages", [])
                result[item["id"]] = item
        return result

    def save_desktop_sessions(self) -> None:
        DESKTOP_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.sessions.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        DESKTOP_SESSIONS_PATH.write_text(
            json.dumps({"current_session_id": self.current_session_id, "sessions": ordered}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def ensure_desktop_session(self) -> str:
        if self.sessions:
            latest = sorted(self.sessions.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)[0]
            return str(latest["id"])
        return self.create_desktop_session()

    def create_desktop_session(self) -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        session_id = time.strftime("desktop-%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        self.sessions[session_id] = {
            "id": session_id,
            "title": "新会话",
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self.save_desktop_sessions()
        return session_id

    def current_session(self) -> dict[str, Any]:
        if self.current_session_id not in self.sessions:
            self.current_session_id = self.create_desktop_session()
        return self.sessions[self.current_session_id]

    def on_close(self) -> None:
        self.save_settings()
        self.destroy()

    def build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "TCombobox",
            padding=6,
            fieldbackground=SOFT_BG,
            background=SOFT_BG,
            foreground=TEXT_MAIN,
            bordercolor=LINE_BG,
            lightcolor=SOFT_BG,
            darkcolor=SOFT_BG,
            arrowcolor=TEXT_MUTED,
        )
        style.map("TCombobox", fieldbackground=[("readonly", SOFT_BG)], bordercolor=[("focus", ACCENT_BLUE)])
        style.configure(
            "TEntry",
            padding=6,
            fieldbackground=SOFT_BG,
            foreground=TEXT_MAIN,
            bordercolor=LINE_BG,
            lightcolor=SOFT_BG,
            darkcolor=SOFT_BG,
        )
        style.configure("TCheckbutton", background=PANEL_BG, foreground=TEXT_MAIN, font=("Microsoft YaHei UI", 9))

    def install_app_cover(self) -> None:
        if not COVER_IMAGE_PATH.exists():
            return
        try:
            self.cover_image = tk.PhotoImage(file=str(COVER_IMAGE_PATH))
        except tk.TclError:
            self.cover_image = None
            return
        try:
            self.iconphoto(True, self.cover_image)
        except tk.TclError:
            pass
        factor = max(1, min(self.cover_image.width(), self.cover_image.height()) // 72)
        self.cover_thumb = self.cover_image.subsample(factor, factor)

    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        nav = tk.Frame(self, bg=WECHAT_DARK, width=72)
        # The old icon nav is kept unmounted; settings are now the only left panel.
        nav.grid_propagate(False)
        tk.Label(nav, text="MC", bg="#1e293b", fg="#eff6ff", font=("Segoe UI", 13, "bold"), width=5, height=2).pack(pady=(18, 18))
        for label, command in [
            ("会话", self.focus_sessions),
            ("架构", self.focus_agent_panel),
            ("工具", self.focus_tool_calls),
            ("日志", self.focus_logs),
            ("设置", self.open_connection_dialog),
        ]:
            button = tk.Button(
                nav,
                text=label,
                command=command,
                bg=WECHAT_DARK,
                fg="#dbe4ea",
                activebackground="#25333a",
                activeforeground="#ffffff",
                relief="flat",
                width=7,
                height=2,
                font=("Microsoft YaHei UI", 9),
            )
            button.pack(pady=3)
            self.bind_button_feedback(button, WECHAT_DARK, "#25333a")

        settings = tk.Frame(self, bg=PANEL_BG, width=304, padx=18, pady=16)
        settings.grid(row=0, column=0, sticky="ns")
        settings.grid_propagate(False)
        self.build_settings(settings)

        main = tk.Frame(self, bg=CHAT_BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        self.build_chat(main)

        right = tk.Frame(self, bg=APP_BG, width=336, padx=12, pady=14)
        # The old architecture panel is no longer mounted in the simplified chat layout.
        right.grid_propagate(False)
        # self.build_agent_panel(right)

    def build_settings(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=PANEL_BG)
        header.pack(fill="x", pady=(0, 16))
        if self.cover_thumb is not None:
            cover_box = tk.Frame(header, bg="#e8f0ff", padx=3, pady=3)
            cover_box.pack(side="left", anchor="n", padx=(0, 12))
            tk.Label(cover_box, image=self.cover_thumb, bg="#e8f0ff", borderwidth=0).pack()
        title_box = tk.Frame(header, bg=PANEL_BG)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text="Mini Claude Code", bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Agent desktop", bg=PANEL_BG, fg=TEXT_FAINT, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 6))
        tk.Label(title_box, textvariable=self.vars["status"], bg="#e8f0ff", fg=ACCENT_BLUE, font=("Microsoft YaHei UI", 9), padx=8, pady=3).pack(anchor="w")

        self.section_label(parent, "Sessions")
        session_card = tk.Frame(parent, bg=SOFT_BG, padx=12, pady=12, highlightthickness=1, highlightbackground="#edf2f7", highlightcolor="#edf2f7")
        session_card.pack(fill="x", pady=(0, 10))
        session_header = tk.Frame(session_card, bg=SOFT_BG)
        session_header.pack(fill="x", pady=(0, 10))
        tk.Label(session_header, text="Recent chats", bg=SOFT_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(session_header, text="local", bg="#e8f0ff", fg=ACCENT_BLUE, font=("Segoe UI", 8, "bold"), padx=7, pady=2).pack(side="right")
        self.session_listbox = tk.Listbox(
            session_card,
            height=6,
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            bg=SOFT_BG,
            fg=TEXT_MAIN,
            selectbackground="#e8f0ff",
            selectforeground=TEXT_MAIN,
            font=("Microsoft YaHei UI", 9),
        )
        self.session_listbox.pack_forget()
        self.session_listbox.bind("<<ListboxSelect>>", self.on_session_select)
        self.session_list_frame = tk.Frame(session_card, bg=SOFT_BG)
        self.session_list_frame.pack(fill="x")
        session_actions = tk.Frame(session_card, bg=SOFT_BG)
        session_actions.pack(fill="x", pady=(10, 0))
        new_button = tk.Button(session_actions, text="+ 新会话", command=self.new_session, bg=ACCENT_BLUE, fg="white", activebackground="#1d4ed8", relief="flat", width=9)
        new_button.configure(text="+ New chat", height=2, padx=10)
        new_button.pack(side="left")
        self.bind_button_feedback(new_button, ACCENT_BLUE, "#1d4ed8")
        delete_button = tk.Button(session_actions, text="清空", command=self.delete_current_session, bg="#edf2f7", fg=TEXT_MUTED, relief="flat", width=8)
        delete_button.configure(text="Clear", height=2, padx=10)
        delete_button.pack(side="left", padx=(8, 0))
        self.bind_button_feedback(delete_button, "#edf2f7", "#e2e8f0")
        self.refresh_session_list()

        self.section_label(parent, "Connection")
        connection_card = tk.Frame(parent, bg=SOFT_BG, padx=12, pady=10, highlightthickness=0)
        connection_card.pack(fill="x", pady=(0, 10))
        tk.Label(connection_card, textvariable=self.vars["provider"], bg=SOFT_BG, fg=ACCENT_BLUE, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(connection_card, text="Model, API, permissions and runtime.", bg=SOFT_BG, fg=TEXT_FAINT, font=("Segoe UI", 9), wraplength=230, justify="left").pack(anchor="w", pady=(3, 10))
        settings_button = tk.Button(
            connection_card,
            text="配置",
            command=self.open_connection_dialog,
            bg=ACCENT_BLUE,
            fg="white",
            activebackground="#1d4ed8",
            relief="flat",
            height=2,
        )
        settings_button.pack(fill="x")
        self.bind_button_feedback(settings_button, ACCENT_BLUE, "#1d4ed8")

        self.separator(parent)
        hint = "Mock 不需要 key。真实接口建议使用 openai provider。"
        tk.Label(parent, text=hint, bg=PANEL_BG, fg=TEXT_FAINT, justify="left", wraplength=250, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

    def build_agent_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Architecture", bg=APP_BG, fg=TEXT_MAIN, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            parent,
            text="Model + Tools + Orchestration + Memory + Runtime + Guardrails",
            bg=APP_BG,
            fg=TEXT_MUTED,
            font=("Segoe UI", 8),
            wraplength=292,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        self.agent_panel_vars = {
            "state": tk.StringVar(value="Idle"),
            "goal": tk.StringVar(value="等待任务输入"),
            "provider": tk.StringVar(value=self.vars["provider"].get()),
            "tools": tk.StringVar(value="0 calls"),
            "latency": tk.StringVar(value="-"),
            "guardrails": tk.StringVar(value="权限 / 密钥脱敏 / Workspace 边界"),
        }
        self.agent_status_card(parent)

        modules = [
            ("Model", "模型推理与最终回复", self.agent_panel_vars["provider"], ACCENT_BLUE),
            ("Tools", "文件、Shell、MCP、HTTP", self.agent_panel_vars["tools"], WECHAT_GREEN),
            ("Orchestration", "Plan → Act → Observe", tk.StringVar(value="S20" if self.vars["s20"].get() else "Basic"), "#7c3aed"),
            ("Memory", "会话、上下文、工具摘要", tk.StringVar(value="Session on"), "#0f766e"),
            ("Runtime", "进程、超时、日志、结果", self.agent_panel_vars["state"], "#b45309"),
            ("Guardrails", "权限、审计、风险控制", tk.StringVar(value="Active"), "#b42318"),
        ]
        grid = tk.Frame(parent, bg=APP_BG)
        grid.pack(fill="x", pady=(0, 12))
        for index, (title, desc, value, color) in enumerate(modules):
            card = tk.Frame(grid, bg=CARD_BG, padx=10, pady=8, highlightthickness=0)
            card.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0 if index % 2 == 0 else 8, 0), pady=(0, 8))
            grid.grid_columnconfigure(index % 2, weight=1)
            tk.Label(card, text=title, bg=CARD_BG, fg=color, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(card, text=desc, bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 8), wraplength=124, justify="left").pack(anchor="w", pady=(3, 5))
            tk.Label(card, textvariable=value, bg=SOFT_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), padx=6, pady=2).pack(anchor="w")

        self.agent_loop_card(parent)
        self.tool_calls_card(parent)
        self.guardrails_card(parent)

    def agent_status_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD_BG, padx=12, pady=10, highlightthickness=0)
        card.pack(fill="x", pady=(0, 12))
        row = tk.Frame(card, bg=CARD_BG)
        row.pack(fill="x")
        tk.Label(row, text="Status", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(row, textvariable=self.agent_panel_vars["state"], bg="#eef3f7", fg=TEXT_MAIN, font=("Segoe UI", 8), padx=8, pady=3).pack(side="right")
        tk.Label(card, text="Goal", bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(anchor="w", pady=(10, 2))
        tk.Label(card, textvariable=self.agent_panel_vars["goal"], bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 9), wraplength=276, justify="left").pack(anchor="w")
        metrics = tk.Frame(card, bg=CARD_BG)
        metrics.pack(fill="x", pady=(10, 0))
        for label, var in [("Tools", self.agent_panel_vars["tools"]), ("Latency", self.agent_panel_vars["latency"])]:
            box = tk.Frame(metrics, bg=SOFT_BG, padx=8, pady=6)
            box.pack(side="left", expand=True, fill="x", padx=(0, 8))
            tk.Label(box, text=label, bg=SOFT_BG, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(box, textvariable=var, bg=SOFT_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(anchor="w")

    def agent_loop_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD_BG, padx=12, pady=10, highlightthickness=0)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, text="Agent Loop", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        steps = ["User Input", "Planning", "Tool Use", "Observation", "Memory", "Final"]
        for index, step in enumerate(steps):
            fg = ACCENT_BLUE if index in {0, 2, 5} else TEXT_MUTED
            tk.Label(card, text=f"{index + 1}. {step}", bg=CARD_BG, fg=fg, font=("Segoe UI", 8)).pack(anchor="w", pady=(6 if index == 0 else 2, 0))

    def tool_calls_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD_BG, padx=12, pady=10, highlightthickness=0)
        card.pack(fill="both", expand=True, pady=(0, 12))
        tk.Label(card, text="Tool Calls", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.tool_call_list = tk.Listbox(
            card,
            height=7,
            borderwidth=0,
            highlightthickness=0,
            bg="#0f172a",
            fg="#e5e7eb",
            selectbackground="#1d4ed8",
            selectforeground="#ffffff",
            font=("Consolas", 8),
        )
        self.tool_call_list.pack(fill="both", expand=True, pady=(8, 0))
        self.refresh_tool_call_panel()

    def guardrails_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD_BG, padx=12, pady=10, highlightthickness=0)
        card.pack(fill="x")
        tk.Label(card, text="Guardrails", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for item in ["Permission policy", "Secret redaction", "Workspace boundary", "Timeout / Stop control"]:
            tk.Label(card, text=f"● {item}", bg=CARD_BG, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 0))

    def refresh_tool_call_panel(self) -> None:
        if self.tool_call_list is None:
            return
        self.tool_call_list.delete(0, "end")
        rows = self.tool_call_rows[-30:] or ["No tool calls yet."]
        for row in rows:
            self.tool_call_list.insert("end", row)
        self.tool_call_list.yview_moveto(1.0)

    def reset_agent_panel_for_run(self, prompt: str) -> None:
        self.run_started_at = time.monotonic()
        self.tool_call_count = 0
        self.tool_call_rows = []
        self.agent_panel_vars["state"].set("Running")
        self.agent_panel_vars["goal"].set(prompt[:180] or "处理附件")
        self.agent_panel_vars["provider"].set(self.vars["provider"].get())
        self.agent_panel_vars["tools"].set("0 calls")
        self.agent_panel_vars["latency"].set("running")
        self.refresh_tool_call_panel()

    def finish_agent_panel_run(self, status: str) -> None:
        self.agent_panel_vars["state"].set(status)
        if self.run_started_at is not None:
            elapsed = max(0, int(time.monotonic() - self.run_started_at))
            self.agent_panel_vars["latency"].set(f"{elapsed}s")
        self.agent_panel_vars["tools"].set(f"{self.tool_call_count} calls")

    def observe_trace_line(self, text: str) -> None:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("[tool] "):
                self.tool_call_count += 1
                self.tool_call_rows.append(line[:180])
                self.agent_panel_vars["tools"].set(f"{self.tool_call_count} calls")
                self.agent_panel_vars["state"].set("Tool Use")
                self.refresh_tool_call_panel()
            elif line.startswith("[tool ok]") or line.startswith("[tool error]"):
                self.tool_call_rows.append(line[:180])
                self.agent_panel_vars["state"].set("Observation")
                self.refresh_tool_call_panel()

    def open_connection_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("接口设置")
        dialog.geometry("520x560")
        dialog.minsize(480, 520)
        dialog.configure(bg="#ffffff")
        dialog.transient(self)
        dialog.grab_set()

        body = tk.Frame(dialog, bg=CARD_BG, padx=24, pady=20)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Connection", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(body, text=f"Local settings: {SETTINGS_PATH}", bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 9), wraplength=450, justify="left").pack(anchor="w", pady=(4, 16))

        self.dialog_combo(body, "Provider", self.vars["provider"], ["mock", "openai", "anthropic"], readonly=True)
        self.dialog_entry(body, "API Key", self.vars["api_key"], show="*")
        self.dialog_combo(
            body,
            "Base URL",
            self.vars["base_url"],
            ["", "https://api.openai.com/v1", "https://api.anthropic.com"],
            readonly=False,
        )
        self.dialog_combo(
            body,
            "Model",
            self.vars["model"],
            ["", "gpt-5.5", "gpt-5", "gpt-4.1", "claude-sonnet-4-6"],
            readonly=False,
        )
        self.dialog_combo(body, "Reasoning", self.vars["reasoning"], ["", "low", "medium", "high", "xhigh"], readonly=True)
        self.separator_dialog(body)
        self.dialog_entry(body, "Workspace", self.vars["workspace"])
        self.dialog_combo(body, "Permission", self.vars["permission"], ["auto", "read-only", "ask", "bypass"], readonly=True)
        self.dialog_combo(body, "运行限制", self.vars["runtime_budget"], ["auto", "unlimited"], readonly=True)
        ttk.Checkbutton(body, text="启用 S20 工具层", variable=self.vars["s20"]).pack(anchor="w", pady=(4, 8))
        ttk.Checkbutton(body, text="显示执行过程/工具轨迹", variable=self.vars["show_trace"]).pack(anchor="w", pady=(0, 8))
        tk.Label(
            body,
            text="说明：auto 会按任务自动判断轮数和超时；unlimited 不限制运行时间、agent 轮数和 shell 命令时间，但需要你手动点击停止来中断卡住的任务。",
            bg=CARD_BG,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
            wraplength=450,
            justify="left",
        ).pack(anchor="w")

        actions = tk.Frame(body, bg=CARD_BG)
        actions.pack(fill="x", pady=(12, 0))
        save_button = tk.Button(actions, text="保存", command=lambda: self.save_dialog(dialog), bg=WECHAT_GREEN, fg="white", relief="flat", width=12, height=2)
        save_button.pack(side="right")
        self.bind_button_feedback(save_button, WECHAT_GREEN, "#06ad56")
        cancel_button = tk.Button(actions, text="取消", command=dialog.destroy, bg="#edf2f7", fg=TEXT_MAIN, relief="flat", width=12, height=2)
        cancel_button.pack(side="right", padx=(0, 10))
        self.bind_button_feedback(cancel_button, "#edf2f7", "#e2e8f0")

    def save_dialog(self, dialog: tk.Toplevel) -> None:
        self.save_settings()
        dialog.destroy()

    def refresh_session_list(self) -> None:
        if self.session_listbox is None:
            return
        self._refreshing_sessions = True
        self.session_listbox.delete(0, "end")
        ordered = sorted(self.sessions.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        for index, session in enumerate(ordered):
            self.session_listbox.insert("end", self.session_title(session))
            if session.get("id") == self.current_session_id:
                self.session_listbox.selection_set(index)
                self.session_listbox.activate(index)
        self._session_order = [str(session["id"]) for session in ordered]
        self.render_session_cards(ordered)
        self._refreshing_sessions = False

    def render_session_cards(self, ordered: list[dict[str, Any]]) -> None:
        if self.session_list_frame is None:
            return
        for child in self.session_list_frame.winfo_children():
            child.destroy()
        for session in ordered[:8]:
            self.add_session_card(self.session_list_frame, session)

    def add_session_card(self, parent: tk.Frame, session: dict[str, Any]) -> None:
        session_id = str(session.get("id") or "")
        active = session_id == self.current_session_id
        bg = "#eef5ff" if active else "#ffffff"
        border = ACCENT_BLUE if active else "#e7ecf2"
        title = str(session.get("title") or "New chat").strip() or "New chat"
        updated = str(session.get("updated_at") or "")[5:16] or "-"
        messages = [item for item in session.get("messages", []) if isinstance(item, dict)]
        count = len(messages)
        preview = self.session_preview(session)
        card = tk.Frame(parent, bg=bg, padx=10, pady=9, highlightthickness=1, highlightbackground=border, highlightcolor=border)
        card.pack(fill="x", pady=(0, 8))
        top = tk.Frame(card, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=title[:20], bg=bg, fg=TEXT_MAIN, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", anchor="w")
        tk.Label(top, text=updated, bg=bg, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(side="right", anchor="e")
        tk.Label(card, text=preview, bg=bg, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 8), anchor="w", justify="left", wraplength=230).pack(fill="x", pady=(5, 7))
        bottom = tk.Frame(card, bg=bg)
        bottom.pack(fill="x")
        status_text = "Current" if active else "Open"
        tk.Label(bottom, text=status_text, bg=ACCENT_BLUE if active else "#f1f5f9", fg="#ffffff" if active else TEXT_MUTED, font=("Segoe UI", 8, "bold"), padx=7, pady=2).pack(side="left")
        tk.Label(bottom, text=f"{count} messages", bg=bg, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(side="right")
        self.bind_session_card_clicks(card, session_id)
        for widget in (card, top, bottom):
            widget.bind("<Button-1>", lambda _event, value=session_id: self.select_session(value))
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda _event, value=session_id: self.select_session(value))
        if not active:
            self.bind_frame_hover(card, "#ffffff", "#f8fbff")

    def bind_session_card_clicks(self, widget: tk.Widget, session_id: str) -> None:
        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda _event, value=session_id: self.select_session(value))
        for child in widget.winfo_children():
            self.bind_session_card_clicks(child, session_id)

    def bind_frame_hover(self, frame: tk.Frame, normal_bg: str, hover_bg: str) -> None:
        def set_bg(widget: tk.Widget, color: str) -> None:
            try:
                current = str(widget.cget("bg"))
                if current in {normal_bg, hover_bg}:
                    widget.configure(bg=color)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                try:
                    set_bg(child, color)
                except tk.TclError:
                    pass

        frame.bind("<Enter>", lambda _event: set_bg(frame, hover_bg))
        frame.bind("<Leave>", lambda _event: set_bg(frame, normal_bg))

    def session_preview(self, session: dict[str, Any]) -> str:
        messages = [item for item in session.get("messages", []) if isinstance(item, dict)]
        for item in reversed(messages):
            if item.get("kind") == "message":
                text = " ".join(str(item.get("text") or "").split())
                if text:
                    return text[:42]
            if item.get("kind") == "attachment":
                paths = item.get("paths", [])
                if isinstance(paths, list) and paths:
                    return f"{len(paths)} attachment(s)"
        return "No messages yet"

    def select_session(self, session_id: str) -> None:
        if self.running:
            self.add_status_line("Current task is running. Switch sessions after it finishes.")
            self.refresh_session_list()
            return
        if not session_id or session_id == self.current_session_id or session_id not in self.sessions:
            return
        self.current_session_id = session_id
        self.chat_title_var.set(self.current_chat_title())
        self.save_settings()
        self.render_current_session()
        self.refresh_session_list()

    def session_title(self, session: dict[str, Any]) -> str:
        title = str(session.get("title") or "新会话").strip()
        updated = str(session.get("updated_at") or "")[5:16]
        return f"{title[:18]}  {updated}".strip()

    def current_chat_title(self) -> str:
        return str(self.current_session().get("title") or "新会话")

    def on_session_select(self, _event: tk.Event[Any]) -> None:
        if self._refreshing_sessions or self.session_listbox is None:
            return
        selection = self.session_listbox.curselection()
        if not selection:
            return
        if self.running:
            self.add_status_line("当前任务运行中，结束后再切换会话。")
            self.refresh_session_list()
            return
        index = int(selection[0])
        session_id = getattr(self, "_session_order", [])[index]
        if session_id == self.current_session_id:
            return
        self.current_session_id = session_id
        self.chat_title_var.set(self.current_chat_title())
        self.save_settings()
        self.render_current_session()

    def new_session(self) -> None:
        if self.running:
            self.add_status_line("当前任务运行中，结束后再新建会话。")
            return
        self.current_session_id = self.create_desktop_session()
        self.chat_title_var.set(self.current_chat_title())
        self.refresh_session_list()
        self.render_current_session()
        self.add_message("agent", "已新建会话。")

    def delete_current_session(self) -> None:
        if self.running:
            self.add_status_line("当前任务运行中，结束后再删除会话。")
            return
        if len(self.sessions) <= 1:
            self.current_session()["messages"] = []
            self.current_session()["title"] = "新会话"
            self.touch_current_session()
        else:
            self.sessions.pop(self.current_session_id, None)
            self.current_session_id = self.ensure_desktop_session()
        self.save_desktop_sessions()
        self.save_settings()
        self.chat_title_var.set(self.current_chat_title())
        self.refresh_session_list()
        self.render_current_session()

    def render_current_session(self) -> None:
        if not hasattr(self, "message_frame"):
            return
        for child in self.message_frame.winfo_children():
            child.destroy()
        for item in self.current_session().get("messages", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind == "message":
                self.add_message(str(item.get("sender") or "agent"), str(item.get("text") or ""), persist=False)
            elif kind == "attachment":
                self.add_attachment_message([str(path) for path in item.get("paths", [])], persist=False)
        self.after(20, self.scroll_chat_to_bottom)

    def touch_current_session(self) -> None:
        self.current_session()["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def record_desktop_message(self, sender: str, text: str) -> None:
        session = self.current_session()
        session.setdefault("messages", []).append(
            {
                "kind": "message",
                "sender": sender,
                "text": text,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if sender == "user" and str(session.get("title") or "新会话") == "新会话":
            session["title"] = " ".join(text.split())[:24] or "新会话"
        self.touch_current_session()
        self.save_desktop_sessions()
        self.chat_title_var.set(self.current_chat_title())
        self.refresh_session_list()

    def record_desktop_attachment(self, paths: list[str]) -> None:
        session = self.current_session()
        session.setdefault("messages", []).append(
            {
                "kind": "attachment",
                "paths": paths,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self.touch_current_session()
        self.save_desktop_sessions()
        self.refresh_session_list()

    def separator_dialog(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg=LINE_BG, height=1).pack(fill="x", pady=10)

    def dialog_entry(self, parent: tk.Frame, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        tk.Label(parent, text=label, bg=CARD_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Entry(parent, textvariable=variable, show=show or "").pack(fill="x", pady=(4, 12))

    def dialog_combo(self, parent: tk.Frame, label: str, variable: tk.StringVar, values: list[str], *, readonly: bool) -> None:
        tk.Label(parent, text=label, bg=CARD_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly" if readonly else "normal").pack(fill="x", pady=(4, 12))

    def bind_button_feedback(self, button: tk.Button, normal_bg: str, hover_bg: str, pressed_bg: str | None = None) -> None:
        pressed = pressed_bg or hover_bg

        def set_bg(color: str) -> None:
            if str(button.cget("state")) != "disabled":
                button.configure(bg=color, activebackground=color)

        button.bind("<Enter>", lambda _event: set_bg(hover_bg))
        button.bind("<Leave>", lambda _event: set_bg(normal_bg))
        button.bind("<ButtonPress-1>", lambda _event: set_bg(pressed))
        button.bind("<ButtonRelease-1>", lambda _event: set_bg(hover_bg))

    def focus_sessions(self) -> None:
        if self.session_listbox is not None:
            self.session_listbox.focus_set()
            self.add_status_line("已定位到会话列表。")

    def focus_agent_panel(self) -> None:
        self.agent_panel_vars["state"].set(self.agent_panel_vars["state"].get())
        self.add_status_line("右侧是 Agent 架构面板。")

    def focus_tool_calls(self) -> None:
        if self.tool_call_list is not None:
            self.tool_call_list.focus_set()
            self.add_status_line("已定位到 Tool Calls。")

    def focus_logs(self) -> None:
        if self.live_trace is not None:
            self.live_trace.focus_set()
            self.live_trace.see("end")
            self.add_status_line("已定位到执行日志。")
            return
        self.add_status_line("当前还没有运行日志。")

    def build_chat(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=CARD_BG, height=72, padx=24)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        title_box = tk.Frame(top, bg=CARD_BG)
        title_box.pack(side="left", fill="y")
        tk.Label(title_box, textvariable=self.chat_title_var, bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(13, 0))
        tk.Label(title_box, text="Model · Tools · Memory · Runtime", bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        tk.Label(top, text="Desktop", bg="#edf4ff", fg=ACCENT_BLUE, font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(side="right", pady=18)

        chat_wrap = tk.Frame(parent, bg=CHAT_BG)
        chat_wrap.grid(row=1, column=0, sticky="nsew")
        chat_wrap.grid_rowconfigure(0, weight=1)
        chat_wrap.grid_columnconfigure(0, weight=1)
        self.chat_canvas = tk.Canvas(chat_wrap, bg=CHAT_BG, highlightthickness=0)
        self.chat_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(chat_wrap, command=self.chat_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.chat_canvas.configure(yscrollcommand=scrollbar.set)
        self.message_frame = tk.Frame(self.chat_canvas, bg=CHAT_BG)
        self.message_window = self.chat_canvas.create_window((0, 0), window=self.message_frame, anchor="nw")
        self.message_frame.bind("<Configure>", self.update_scroll_region)
        self.chat_canvas.bind("<Configure>", self.resize_message_frame)
        chat_wrap.bind("<Enter>", self.enable_chat_mousewheel)
        chat_wrap.bind("<Leave>", self.disable_chat_mousewheel)
        self.chat_canvas.bind("<Enter>", self.enable_chat_mousewheel)
        self.message_frame.bind("<Enter>", self.enable_chat_mousewheel)

        input_bar = tk.Frame(parent, bg=CARD_BG, padx=18, pady=14, highlightthickness=0)
        input_bar.grid(row=2, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)
        self.attachment_tray = tk.Frame(input_bar, bg=SOFT_BG, padx=10, pady=8, highlightthickness=0)
        self.attachment_tray.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.attachment_tray.grid_remove()
        tray_header = tk.Frame(self.attachment_tray, bg=SOFT_BG)
        tray_header.pack(fill="x")
        tk.Label(tray_header, text="Attachments", bg=SOFT_BG, fg=TEXT_MAIN, font=("Segoe UI", 9, "bold")).pack(side="left")
        clear_button = tk.Button(tray_header, text="Clear", command=self.clear_pending_attachments, bg=SOFT_BG, fg=TEXT_MUTED, activebackground="#e8edf2", relief="flat", padx=6, pady=2)
        clear_button.pack(side="right")
        self.bind_button_feedback(clear_button, SOFT_BG, "#e8edf2")
        self.attachment_chips = tk.Frame(self.attachment_tray, bg=SOFT_BG)
        self.attachment_chips.pack(fill="x", pady=(6, 0))

        self.prompt_text = tk.Text(input_bar, height=4, wrap="word", font=("Segoe UI", 10), relief="flat", bg=SOFT_BG, padx=14, pady=12, highlightthickness=0, insertbackground=TEXT_MAIN)
        self.prompt_text.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        if self.initial_prompt:
            self.prompt_text.insert("1.0", self.initial_prompt)
        else:
            self.show_prompt_placeholder()
        self.prompt_text.bind("<Return>", self.on_prompt_return)
        self.prompt_text.bind("<Shift-Return>", self.on_prompt_shift_return)
        self.prompt_text.bind("<FocusIn>", self.on_prompt_focus_in)
        self.prompt_text.bind("<FocusOut>", self.on_prompt_focus_out)
        self.setup_file_drop(self.prompt_text)
        self.setup_file_drop(self.chat_canvas)
        self.setup_file_drop(self.message_frame)
        button_col = tk.Frame(input_bar, bg=CARD_BG)
        button_col.grid(row=1, column=1, sticky="ns")
        self.run_button = tk.Button(button_col, text="运行", command=self.run_agent, bg=ACCENT_BLUE, fg="white", activebackground="#1d4ed8", relief="flat", width=10, height=2)
        self.run_button.pack()
        self.bind_button_feedback(self.run_button, ACCENT_BLUE, "#1d4ed8")
        file_button = tk.Button(button_col, text="附件", command=self.choose_files, bg="#edf2f7", fg=TEXT_MAIN, activebackground="#e2e8f0", relief="flat", width=10, height=2)
        file_button.pack(pady=(8, 0))
        self.bind_button_feedback(file_button, "#edf2f7", "#e2e8f0")
        tk.Label(button_col, text="Enter 运行\nShift+Enter 换行", bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 8), justify="center").pack(pady=(8, 0))
        if not DND_AVAILABLE:
            tk.Label(button_col, text="拖拽需安装\ntkinterdnd2", bg=CARD_BG, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 8), justify="center").pack(pady=(6, 0))

    def section_label(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, bg=PANEL_BG, fg=TEXT_MAIN, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(14, 10))

    def separator(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg="#d9dee3", height=1).pack(fill="x", pady=14)

    def entry(self, parent: tk.Frame, label: str, variable: tk.StringVar, show: str | None = None, *, compact: bool = False) -> None:
        tk.Label(parent, text=label, bg=PANEL_BG, fg="#53606c", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        ttk.Entry(parent, textvariable=variable, show=show or "").pack(fill="x", pady=(4, 10 if not compact else 0))

    def combo(self, parent: tk.Frame, label: str, variable: tk.StringVar, values: list[str]) -> None:
        tk.Label(parent, text=label, bg=PANEL_BG, fg="#53606c", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").pack(fill="x", pady=(4, 10))

    def update_scroll_region(self, _event: tk.Event[Any] | None = None) -> None:
        bbox = self.chat_canvas.bbox("all")
        canvas_width = max(1, self.chat_canvas.winfo_width())
        canvas_height = max(1, self.chat_canvas.winfo_height())
        if bbox is None:
            self.chat_canvas.configure(scrollregion=(0, 0, canvas_width, canvas_height))
            return
        content_width = max(canvas_width, bbox[2] - bbox[0])
        content_height = max(canvas_height, bbox[3] - bbox[1])
        self.chat_canvas.coords(self.message_window, 0, 0)
        self.chat_canvas.configure(scrollregion=(0, 0, content_width, content_height))

    def resize_message_frame(self, event: tk.Event[Any]) -> None:
        self.chat_canvas.itemconfigure(self.message_window, width=event.width)

    def enable_chat_mousewheel(self, _event: tk.Event[Any] | None = None) -> None:
        self.bind_all("<MouseWheel>", self.on_chat_mousewheel)

    def disable_chat_mousewheel(self, _event: tk.Event[Any] | None = None) -> None:
        self.unbind_all("<MouseWheel>")

    def on_chat_mousewheel(self, event: tk.Event[Any]) -> None:
        self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def scroll_chat_to_bottom(self) -> None:
        self.update_scroll_region()
        bbox = self.chat_canvas.bbox("all")
        if bbox is not None and bbox[3] > self.chat_canvas.winfo_height():
            self.chat_canvas.yview_moveto(1.0)

    def add_message(self, sender: str, text: str, *, persist: bool = True) -> None:
        outer = tk.Frame(self.message_frame, bg=CHAT_BG, padx=18, pady=8)
        outer.pack(fill="x", anchor="w")
        is_user = sender == "user"
        bubble_bg = ACCENT_BLUE if is_user else CARD_BG
        bubble_fg = "white" if is_user else TEXT_MAIN
        side = "e" if is_user else "w"
        name = "你" if is_user else "Mini Claude Code"
        tk.Label(outer, text=name, bg=CHAT_BG, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(anchor="e" if is_user else "w")
        bubble = tk.Text(
            outer,
            bg=bubble_bg,
            fg=bubble_fg,
            wrap="word",
            width=min(80, max(18, max((len(line) for line in text.splitlines()), default=18))),
            height=self.bubble_height(text),
            padx=14,
            pady=10,
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            insertwidth=0,
            selectbackground="#93c5fd" if is_user else "#c9d7e3",
            selectforeground=bubble_fg,
        )
        bubble.insert("1.0", text)
        bubble.configure(state="disabled", cursor="arrow")
        bubble.bind("<Button-3>", lambda event, value=text: self.show_copy_menu(event, value))
        bubble.bind("<Control-c>", lambda _event, widget=bubble: self.copy_selection_or_text(widget, text))
        bubble.bind("<Double-Button-1>", lambda _event, value=text: self.copy_text(value))
        bubble.pack(anchor=side, pady=(3, 0))
        if persist:
            self.record_desktop_message(sender, text)
        self.after(20, self.scroll_chat_to_bottom)

    def bubble_height(self, text: str) -> int:
        lines = text.splitlines() or [""]
        visual_lines = 0
        for line in lines:
            visual_lines += max(1, (len(line) + 55) // 56)
        return max(1, min(18, visual_lines))

    def show_copy_menu(self, event: tk.Event[Any], text: str) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="复制内容", command=lambda: self.copy_text(text))
        menu.tk_popup(event.x_root, event.y_root)

    def copy_selection_or_text(self, widget: tk.Text, fallback: str) -> str:
        try:
            selected = widget.get("sel.first", "sel.last")
        except tk.TclError:
            selected = fallback
        self.copy_text(selected)
        return "break"

    def copy_text(self, text: str) -> str:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.add_status_line("已复制。")
        return "break"

    def add_status_line(self, text: str) -> None:
        outer = tk.Frame(self.message_frame, bg=CHAT_BG, padx=18, pady=4)
        outer.pack(fill="x", anchor="center")
        tk.Label(
            outer,
            text=text,
            bg="#edf2f7",
            fg=TEXT_MUTED,
            font=("Segoe UI", 8),
            padx=10,
            pady=4,
        ).pack(anchor="center")
        self.after(20, self.scroll_chat_to_bottom)

    def setup_file_drop(self, widget: tk.Widget) -> None:
        if not DND_AVAILABLE:
            return
        try:
            widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            widget.dnd_bind("<<Drop>>", self.on_file_drop)  # type: ignore[attr-defined]
        except tk.TclError as exc:
            self.log_event(f"drop_setup_failed {exc}")

    def choose_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择要加入聊天的文件")
        if paths:
            self.attach_files([str(path) for path in paths])

    def on_file_drop(self, event: tk.Event[Any]) -> str:
        raw = str(getattr(event, "data", "") or "")
        try:
            paths = [str(item) for item in self.tk.splitlist(raw)]
        except tk.TclError:
            paths = [raw]
        self.attach_files(paths)
        return "break"

    def attach_files(self, paths: list[str]) -> None:
        cleaned = [str(Path(path.strip("{}")).expanduser()) for path in paths if str(path).strip()]
        added = 0
        for path in cleaned:
            if path not in self.pending_attachments:
                self.pending_attachments.append(path)
                added += 1
        if not self.pending_attachments:
            return
        self.refresh_pending_attachments()
        self.prompt_text.focus_set()
        self.add_status_line(f"已添加 {added or len(cleaned)} 个附件，可在下方附件栏移除。")

    def refresh_pending_attachments(self) -> None:
        if self.attachment_tray is None or self.attachment_chips is None:
            return
        for child in self.attachment_chips.winfo_children():
            child.destroy()
        if not self.pending_attachments:
            self.attachment_tray.grid_remove()
            return
        self.attachment_tray.grid()
        for index, path in enumerate(self.pending_attachments):
            chip = tk.Frame(self.attachment_chips, bg="#edf4ff", padx=9, pady=6, highlightthickness=0)
            chip.pack(side="left", padx=(0, 8), pady=(0, 6))
            suffix = Path(path).suffix.lower()
            kind = "图片" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} else "文件"
            tk.Label(chip, text=f"{kind}  {Path(path).name or path}", bg="#edf4ff", fg=TEXT_MAIN, font=("Segoe UI", 9)).pack(side="left")
            remove_button = tk.Button(
                chip,
                text="x",
                command=lambda item=path: self.remove_pending_attachment(item),
                bg="#edf4ff",
                fg=TEXT_MUTED,
                activebackground="#dbeafe",
                relief="flat",
                padx=5,
                pady=0,
            )
            remove_button.pack(side="left", padx=(8, 0))
            self.bind_button_feedback(remove_button, "#edf4ff", "#dbeafe")

    def remove_pending_attachment(self, path: str) -> None:
        self.pending_attachments = [item for item in self.pending_attachments if item != path]
        self.refresh_pending_attachments()
        self.add_status_line("已移除附件。")

    def clear_pending_attachments(self) -> None:
        self.pending_attachments = []
        self.refresh_pending_attachments()
        self.add_status_line("已清空附件。")

    def file_prompt_block(self, paths: list[str]) -> str:
        lines = ["[附件文件]"]
        for path in paths:
            suffix = Path(path).suffix.lower()
            kind = "图片" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} else "文件"
            lines.append(f"- {kind}: {path}")
        lines.append("请在回答或执行任务时把这些附件路径作为上下文。")
        return "\n".join(lines)

    def add_attachment_message(self, paths: list[str], *, persist: bool = True) -> None:
        outer = tk.Frame(self.message_frame, bg=CHAT_BG, padx=18, pady=6)
        outer.pack(fill="x", anchor="e")
        card = tk.Frame(outer, bg=CARD_BG, padx=12, pady=8, highlightthickness=0)
        card.pack(anchor="e")
        tk.Label(card, text=f"{len(paths)} attachments", bg=CARD_BG, fg=TEXT_MAIN, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        for path in paths[:5]:
            tk.Label(card, text=Path(path).name or path, bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 8), wraplength=360, justify="left").pack(anchor="w")
        if len(paths) > 5:
            tk.Label(card, text=f"+{len(paths) - 5} more", bg=CARD_BG, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(anchor="w")
        if persist:
            self.record_desktop_attachment(paths)
        self.after(20, self.scroll_chat_to_bottom)

    def add_agent_result(self, summary: str, trace_lines: list[str]) -> None:
        self.add_message("agent", summary)
        if not trace_lines:
            return
        outer = tk.Frame(self.message_frame, bg=CHAT_BG, padx=18)
        outer.pack(fill="x", anchor="w", pady=(0, 8))
        holder = tk.Frame(outer, bg=CHAT_BG)
        holder.pack(anchor="w")
        expanded = tk.BooleanVar(value=self.vars["show_trace"].get())
        trace_text = "\n\n".join(f"{index}. {line}" for index, line in enumerate(trace_lines[-20:], start=1))
        details = tk.Text(
            holder,
            width=92,
            height=12,
            wrap="word",
            font=("Consolas", 9),
            bg="#111827",
            fg="#e5e7eb",
            relief="flat",
            padx=10,
            pady=8,
        )
        details.insert("1.0", trace_text)
        details.configure(state="disabled")

        def toggle() -> None:
            if expanded.get():
                details.pack_forget()
                expanded.set(False)
                button.configure(text="展开执行过程")
            else:
                details.pack(anchor="w", pady=(6, 0))
                expanded.set(True)
                button.configure(text="收起执行过程")
                self.after(20, self.scroll_chat_to_bottom)

        button = tk.Button(
            holder,
            text="收起执行过程" if expanded.get() else "展开执行过程",
            command=toggle,
            bg="#e8edf2",
            fg=TEXT_MAIN,
            activebackground="#d9e1e8",
            relief="flat",
            padx=10,
            pady=4,
        )
        button.pack(anchor="w")
        if expanded.get():
            details.pack(anchor="w", pady=(6, 0))
        self.after(20, self.scroll_chat_to_bottom)

    def add_live_trace_panel(self) -> tk.Text:
        outer = tk.Frame(self.message_frame, bg=CHAT_BG, padx=18)
        outer.pack(fill="x", anchor="w", pady=(0, 8))
        holder = tk.Frame(outer, bg=CHAT_BG)
        holder.pack(anchor="w", fill="x")
        tk.Label(holder, text="执行过程", bg=CHAT_BG, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        details = tk.Text(
            holder,
            width=92,
            height=10,
            wrap="word",
            font=("Consolas", 9),
            bg="#111827",
            fg="#e5e7eb",
            relief="flat",
            padx=10,
            pady=8,
        )
        details.pack(anchor="w", fill="x", pady=(4, 0))
        details.insert("1.0", "正在启动本地 agent...\n")
        details.configure(state="disabled")
        self.after(20, self.scroll_chat_to_bottom)
        return details

    def append_live_trace(self, text: str) -> None:
        self.observe_trace_line(text)
        if self.live_trace is None:
            return
        self.live_trace.configure(state="normal")
        self.live_trace.insert("end", text)
        self.live_trace.see("end")
        self.live_trace.configure(state="disabled")
        self.after(20, self.scroll_chat_to_bottom)

    def log_event(self, message: str) -> None:
        RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with RUN_LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def redacted_command(self, command: list[str]) -> str:
        return " ".join(str(part) for part in command)

    def prompt_value(self) -> str:
        if getattr(self, "prompt_placeholder_active", False):
            return ""
        return self.prompt_text.get("1.0", "end").strip()

    def show_prompt_placeholder(self) -> None:
        self.prompt_placeholder_active = True
        self.prompt_text.configure(fg=TEXT_MUTED)
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", PROMPT_PLACEHOLDER)

    def hide_prompt_placeholder(self) -> None:
        if not getattr(self, "prompt_placeholder_active", False):
            return
        self.prompt_placeholder_active = False
        self.prompt_text.configure(fg=TEXT_MAIN)
        self.prompt_text.delete("1.0", "end")

    def on_prompt_focus_in(self, _event: tk.Event[Any]) -> None:
        self.hide_prompt_placeholder()

    def on_prompt_focus_out(self, _event: tk.Event[Any]) -> None:
        if not self.prompt_text.get("1.0", "end").strip():
            self.show_prompt_placeholder()

    def on_prompt_return(self, _event: tk.Event[Any]) -> str:
        self.log_event("prompt_enter_pressed")
        self.run_agent()
        return "break"

    def on_prompt_shift_return(self, _event: tk.Event[Any]) -> str:
        self.prompt_text.insert("insert", "\n")
        return "break"

    def run_agent(self) -> None:
        self.log_event("send_clicked")
        try:
            self.run_agent_inner()
        except Exception:
            detail = traceback.format_exc()
            self.log_event("send_exception " + detail)
            self.add_status_line("发送流程异常，已写入日志：" + str(RUN_LOG_PATH))

    def run_agent_inner(self) -> None:
        if self.running:
            self.log_event("send_ignored already_running")
            return
        visible_prompt = self.prompt_value()
        attachments = list(self.pending_attachments)
        prompt = self.compose_user_prompt(visible_prompt, attachments)
        self.log_event(f"prompt_loaded chars={len(prompt)} attachments={len(attachments)}")
        if not prompt:
            self.log_event("send_ignored empty_prompt")
            self.add_status_line("请先输入任务。")
            return
        try:
            command, env, timeout, budget_text = self.build_command(prompt)
        except ValueError as exc:
            self.log_event(f"command_value_error {exc}")
            self.add_status_line(str(exc))
            return
        self.log_event("command_built " + budget_text)
        self.running = True
        self.save_settings()
        self.log_event("settings_saved")
        self.run_button.configure(state="normal", text="停止", command=self.cancel_run)
        self.vars["status"].set("运行中")
        self.reset_agent_panel_for_run(visible_prompt or "请处理这些附件。")
        self.add_message("user", visible_prompt or "请处理这些附件。")
        if attachments:
            self.add_attachment_message(attachments)
        self.log_event("user_message_added")
        self.prompt_text.delete("1.0", "end")
        self.show_prompt_placeholder()
        self.pending_attachments = []
        self.refresh_pending_attachments()
        if self.vars["show_trace"].get():
            self.add_status_line("正在运行。" + budget_text)
        else:
            self.add_status_line("正在运行。")
        self.log_event("agent_ack_added")
        self.live_trace = self.add_live_trace_panel()
        self.log_event("live_trace_panel_added")
        provider = self.vars["provider"].get()
        self.log_event(f"run_start provider={provider} {budget_text} command={self.redacted_command(command)}")
        self.append_live_trace(
            "[desktop] 已启动任务\n"
            f"[desktop] Provider: {provider}\n"
            f"[desktop] {budget_text}\n"
            f"[desktop] 日志文件: {RUN_LOG_PATH}\n"
            f"[desktop] 命令: {self.redacted_command(command)}\n"
        )
        threading.Thread(target=self.worker, args=(command, env, timeout), daemon=True).start()

    def compose_user_prompt(self, prompt: str, attachments: list[str]) -> str:
        parts = [prompt.strip()] if prompt.strip() else []
        if attachments:
            parts.append(self.file_prompt_block(attachments))
        return "\n\n".join(parts).strip()

    def cancel_run(self) -> None:
        process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()
            self.append_live_trace("\n[desktop] 已请求停止当前运行。\n")
            self.add_status_line("已请求停止当前运行。")
            self.finish_agent_panel_run("Stopping")

    def build_command(self, prompt: str) -> tuple[list[str], dict[str, str], int, str]:
        provider = self.vars["provider"].get()
        max_turns, timeout, budget_text = self.auto_runtime_budget(prompt)
        workspace = Path(self.vars["workspace"].get() or REPO_ROOT).expanduser().resolve()
        permission = self.vars["permission"].get() or "auto"
        command = [
            str(CHILD_PYTHON),
            "-m",
            "mini_cc",
            "run",
            "--workspace",
            str(workspace),
            "--permission-mode",
            permission,
            "--max-turns",
            str(max_turns),
            "--prompt",
            self.desktop_agent_prompt(prompt),
        ]
        if max_turns <= 0 or timeout <= 0:
            command.extend(["--shell-timeout", "0"])
        if self.should_enable_s20(prompt):
            command.append("--s20")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if provider == "mock":
            command.append("--mock")
        else:
            api_key = self.vars["api_key"].get().strip()
            if not api_key:
                raise ValueError("非 Mock 模式需要填写 API key。")
            command.extend(["--provider", provider])
            model = self.vars["model"].get().strip()
            base_url = self.vars["base_url"].get().strip()
            reasoning = self.vars["reasoning"].get().strip()
            if model:
                command.extend(["--model", model])
            if base_url:
                command.extend(["--base-url", base_url])
            if provider == "openai":
                env["OPENAI_API_KEY"] = api_key
                if reasoning:
                    command.extend(["--reasoning-effort", reasoning])
            else:
                env["ANTHROPIC_API_KEY"] = api_key
        return command, env, timeout, budget_text

    def should_enable_s20(self, prompt: str) -> bool:
        if not self.vars["s20"].get():
            return False
        lowered = prompt.lower().strip()
        simple_chat = {
            "你好",
            "您好",
            "hello",
            "hi",
            "hey",
            "在吗",
            "你是谁",
        }
        if lowered in simple_chat or len(lowered) <= 8:
            return False
        tool_tokens = [
            "读取",
            "搜索",
            "文件",
            "项目",
            "代码",
            "修改",
            "实现",
            "测试",
            "打开",
            "运行",
            "创建",
            "桌面",
            "vscode",
            "vs code",
            "benchmark",
            "terminal-bench",
            "swe-bench",
            "read",
            "search",
            "file",
            "code",
            "test",
        ]
        return any(token in lowered for token in tool_tokens)

    def auto_runtime_budget(self, prompt: str) -> tuple[int, int, str]:
        if self.vars["runtime_budget"].get() == "unlimited":
            return 0, 0, "运行预算：无限制，不限制 agent 轮数、运行时间和 shell 命令时间。"
        lowered = prompt.lower()
        long_tokens = [
            "benchmark",
            "terminal-bench",
            "swe-bench",
            "docker",
            "全量",
            "跑完",
            "评测",
            "基准",
        ]
        write_tokens = [
            "修改",
            "实现",
            "修复",
            "优化",
            "写代码",
            "创建文件",
            "测试",
            "打开",
            "运行",
            "桌面",
            "vscode",
            "vs code",
            "run tests",
            "fix",
            "implement",
            "refactor",
        ]
        read_tokens = [
            "读取",
            "搜索",
            "总结",
            "分析",
            "列出",
            "查看",
            "read",
            "search",
            "summarize",
            "list",
        ]
        if any(token in lowered for token in long_tokens):
            return 14, 900, "自动运行预算：长任务，最多 14 轮，最长 900 秒。"
        if any(token in lowered for token in write_tokens):
            return 8, 300, "自动运行预算：工程任务，最多 8 轮，最长 300 秒。"
        if any(token in lowered for token in read_tokens):
            return 6, 180, "自动运行预算：读取/分析任务，最多 6 轮，最长 180 秒。"
        if self.vars["s20"].get():
            return 8, 180, "自动运行预算：普通对话，最多 8 轮，最长 180 秒。"
        return 4, 90, "自动运行预算：轻量对话，最多 4 轮，最长 90 秒。"

    def desktop_agent_prompt(self, prompt: str) -> str:
        return (
            "你正在 Mini Claude Code 桌面软件里运行。"
            "如果用户要求创建、修改、打开文件、运行程序、打开 VS Code、执行命令或检查结果，"
            "你必须优先使用可用工具实际执行；只有工具不可用、权限被拒绝或执行失败时，才改为给用户步骤。"
            "不要说“我不能直接操作你的电脑”来替代工具调用。"
            "如果执行了操作，最终回答要说明实际做了什么、结果是什么、证据来自哪个工具输出。\n\n"
            "用户原始请求：\n"
            f"{prompt}"
        )

    def worker(self, command: list[str], env: dict[str, str], timeout: int) -> None:
        stdout_parts: list[str] = []
        stderr_text = ""
        start = time.monotonic()
        last_heartbeat = start
        try:
            self.log_event("process_spawn")
            process = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                bufsize=1,
            )
            self.current_process = process
            self.log_event(f"process_started pid={process.pid}")
            assert process.stdout is not None
            while True:
                if timeout > 0 and time.monotonic() - start > timeout and process.poll() is None:
                    process.kill()
                    self.log_event(f"process_timeout timeout={timeout}")
                    self.result_queue.put(
                        {
                            "timeout": timeout,
                            "stdout": "".join(stdout_parts),
                            "stderr": stderr_text,
                        }
                    )
                    return
                line = process.stdout.readline()
                if line:
                    stdout_parts.append(line)
                    self.log_event("stdout " + line.rstrip())
                    self.result_queue.put({"stream": "stdout", "text": line})
                    continue
                if process.poll() is not None:
                    break
                now = time.monotonic()
                if now - last_heartbeat >= 3:
                    elapsed = int(now - start)
                    heartbeat = f"[desktop] 仍在运行，已耗时 {elapsed} 秒；通常是在等待模型或工具返回...\n"
                    self.log_event(heartbeat.rstrip())
                    self.result_queue.put({"stream": "stdout", "text": heartbeat})
                    last_heartbeat = now
                time.sleep(0.05)
            remaining = process.stdout.read()
            if remaining:
                stdout_parts.append(remaining)
                self.log_event("stdout " + remaining.rstrip())
                self.result_queue.put({"stream": "stdout", "text": remaining})
            if process.stderr is not None:
                stderr_text = process.stderr.read()
                if stderr_text:
                    self.log_event("stderr " + stderr_text.rstrip())
                    self.result_queue.put({"stream": "stderr", "text": stderr_text})
            self.log_event(f"process_completed returncode={process.returncode} elapsed={int(time.monotonic() - start)}s")
            self.result_queue.put(
                {
                    "completed": {
                        "returncode": process.returncode,
                        "stdout": "".join(stdout_parts),
                        "stderr": stderr_text,
                    }
                }
            )
        except Exception as exc:
            self.log_event(f"process_error {exc}")
            self.result_queue.put({"error": str(exc)})
        finally:
            self.current_process = None

    def poll_result_queue(self) -> None:
        handled_final = False
        try:
            while True:
                result = self.result_queue.get_nowait()
                if "stream" in result:
                    prefix = "" if result["stream"] == "stdout" else "\n[stderr]\n"
                    self.append_live_trace(prefix + str(result.get("text", "")))
                    continue
                self.running = False
                self.run_button.configure(state="normal", text="发送", command=self.run_agent)
                self.render_result(result)
                handled_final = True
        except queue.Empty:
            self.after(120, self.poll_result_queue)
            return
        if not handled_final:
            self.after(120, self.poll_result_queue)
        else:
            self.after(120, self.poll_result_queue)

    def render_result(self, result: dict[str, Any]) -> None:
        if "timeout" in result:
            self.vars["status"].set("超时")
            self.finish_agent_panel_run("Timeout")
            self.add_status_line(f"运行超时：{result['timeout']} 秒。详情可查看执行过程和日志。")
            return
        if "error" in result:
            self.vars["status"].set("失败")
            self.finish_agent_panel_run("Error")
            self.add_status_line(f"运行失败：{result['error']}")
            return
        completed = result["completed"]
        returncode = int(completed.get("returncode", 1))
        stdout = str(completed.get("stdout", ""))
        stderr = str(completed.get("stderr", ""))
        ok = returncode == 0
        self.vars["status"].set("完成" if ok else "失败")
        self.finish_agent_panel_run("Completed" if ok else "Error")
        if "Stopped after max_turns" in stdout:
            self.finish_agent_panel_run("Max turns")
            self.add_status_line("本次运行被轮数预算截断。详情可查看执行过程。")
        elif ok:
            reply = self.extract_assistant_reply(stdout)
            if reply:
                self.add_message("agent", reply)
            self.add_status_line("运行完成。")
        else:
            detail = (stderr or stdout or "").strip()
            if detail:
                self.append_live_trace("\n[desktop] 失败详情\n" + detail[:2500] + "\n")
            self.add_status_line("运行失败。详情可查看执行过程和日志。")

    def extract_assistant_reply(self, stdout: str) -> str:
        segments: list[list[str]] = []
        current: list[str] = []
        in_tool_block = False

        def flush_current() -> None:
            while current and not current[-1].strip():
                current.pop()
            if current:
                segments.append(list(current))
                current.clear()

        for raw_line in stdout.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if in_tool_block:
                    in_tool_block = False
                    continue
                if current:
                    current.append("")
                continue
            if self.is_execution_line(stripped):
                flush_current()
                in_tool_block = stripped.startswith("[tool")
                continue
            if in_tool_block:
                continue
            current.append(line)
        flush_current()

        for segment in reversed(segments):
            text = "\n".join(segment).strip()
            if self.is_user_visible_reply(text):
                return text[:2500]
        return ""

    def is_execution_line(self, text: str) -> bool:
        prefixes = (
            "[desktop]",
            "[tool]",
            "[tool ok]",
            "[tool error]",
            "[stderr]",
            "Stopped after max_turns=",
            "Mini Claude Code REPL.",
            "mini-cc>",
        )
        return text.startswith(prefixes)

    def is_user_visible_reply(self, text: str) -> bool:
        if not text:
            return False
        status_texts = {
            "运行完成。",
            "运行失败。",
            "正在运行。",
        }
        if text in status_texts:
            return False
        if text.startswith("{") and text.endswith("}"):
            return False
        if text.startswith("Traceback "):
            return False
        return True

    def parse_agent_output(self, stdout: str, stderr: str, returncode: int) -> tuple[str, list[str]]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            text = stdout.strip() or stderr.strip() or f"运行结束，returncode={returncode}"
            return text[:2500], []
        if not isinstance(payload, dict):
            return stdout[:2500], []
        if not payload.get("ok", returncode == 0):
            return (payload.get("error") or stderr or "运行失败。")[:2500], self.trace_lines(payload)
        trace = payload.get("trace")
        if isinstance(trace, list) and trace:
            lines = self.trace_lines(payload)
            if any("Stopped after max_turns" in line for line in lines):
                return "本次运行被轮数预算截断了。已调大自动预算，请重新发送这条消息。", lines
            visible = [line for line in lines if not line.startswith("[tool") and not line.startswith("{")]
            return (visible[-1] if visible else lines[-1])[:2500], lines
        return "运行完成。", []

    def trace_lines(self, payload: dict[str, Any]) -> list[str]:
        trace = payload.get("trace")
        if not isinstance(trace, list):
            return []
        return [str(item).strip() for item in trace if str(item).strip()]

    def clean_int(self, value: str, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except ValueError:
            parsed = default
        return max(minimum, min(maximum, parsed))


def main() -> int:
    app = MiniCCDesktopApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

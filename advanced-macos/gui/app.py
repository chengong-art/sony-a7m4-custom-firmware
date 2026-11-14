#!/usr/bin/env python3
"""A7M4 ToolKit window.

The window is a client of the same JSON-RPC interface agents use
(`a7m4 agent`, documented in docs/agent-api.md). It does not reimplement a single
guard: it asks for `capabilities` to discover the method catalog, renders one
button per method grouped by the catalog's own safety level, sends the parameters
you edit, and shows the progress notifications and results exactly as an agent
would receive them.

That is also why there is no terminal emulation and no prompt to answer: methods
take parameters instead of asking questions, so nothing can block waiting for a
keystroke.

`AgentClient` has no Tk dependency, so the protocol plumbing is testable headlessly.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

TITLE = "A7M4 ToolKit"

SAFETY_ORDER = ["read", "key", "session", "write"]
SAFETY_LABEL = {
    "read": "只读",
    "key": "写密钥",
    "session": "接触相机（不写入）",
    "write": "写相机",
}

# One palette, chosen from the system appearance, applied to every widget. Mixing
# Tk defaults with hand-picked colours is how an unreadable window happens: the
# system is dark, so default label text is light, while a hardcoded light
# background stays light.
LIGHT = {
    "window": "#f2f3f5", "panel": "#ffffff", "field": "#ffffff", "border": "#c9ced6",
    "text": "#1d1f23", "muted": "#5f6672", "hint": "#8a6d3b",
    "log_bg": "#ffffff", "log_fg": "#1d1f23",
    "ok": "#1a7f37", "bad": "#b3261e", "warn": "#8a4b00", "key": "#7a5c00",
    "accent": "#6c4bb6", "you": "#1f5fbf", "progress": "#5f6672",
}
DARK = {
    "window": "#1e1f22", "panel": "#26282c", "field": "#1b1d21", "border": "#3a3d42",
    "text": "#e6e8eb", "muted": "#9aa3ad", "hint": "#d0a45b",
    "log_bg": "#131417", "log_fg": "#d8dee9",
    "ok": "#7ddc8b", "bad": "#ff8a80", "warn": "#e0a35c", "key": "#d8c07a",
    "accent": "#c792ea", "you": "#82aaff", "progress": "#8b95a1",
}
SAFETY_KEY = {"read": "ok", "key": "key", "session": "warn", "write": "bad"}

# Display names. The method id stays the contract (`status`, `apply`, ...); these
# are only what the window shows, so an agent and a person read the same request.
METHOD_LABELS = {
    "capabilities": "查看能力表",
    "status": "查看状态",
    "doctor": "检查资源包",
    "connect_backup": "连接并完整备份",
    "inspect": "读取版本信息",
    "keygen": "生成密钥对",
    "prepare": "准备镜像（不写相机）",
    "apply": "写入相机",
    "reboot": "退出 fastboot",
}
STATE_LABELS = {
    "disconnected": "未连接",
    "connecting": "连接中",
    "backing_up": "备份中",
    "ready": "就绪",
    "busy": "忙",
}
ERROR_LABELS = {
    "PARAMS": "参数不合法",
    "METHOD": "没有这个方法",
    "REQUEST": "请求格式不对",
    "REQUEST_SIZE": "请求过大",
    "INVALID_REQUEST": "JSON 无效",
    "BACKUP_REQUIRED": "需要本次会话的完整备份",
    "BUSY": "另一个进程正在使用相机",
    "LOCK": "没有持有设备锁",
    "LOCK_LOST": "设备锁已失效",
    "ASSETS_MISSING": "资源包缺失",
    "ASSET_INTEGRITY": "资源包哈希不符",
    "ASSET_PERMISSION": "资源文件不可执行",
    "USB_COMMUNICATION": "USB 通信失败",
    "DISK_SPACE": "磁盘空间不足",
    "CONFIRMATION": "确认串不匹配",
    "INTERNAL": "内部错误",
}


def method_label(name: str) -> str:
    return METHOD_LABELS.get(name, name)


def state_label(value: str) -> str:
    return STATE_LABELS.get(value, value)


def error_label(code: str) -> str:
    return ERROR_LABELS.get(code, code)


def detect_dark() -> bool:
    """macOS records the appearance here; there is no portable Tk query."""
    try:
        result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    return result.returncode == 0 and "dark" in result.stdout.strip().lower()

# Template defaults for known parameters. The server validates everything, so
# these are conveniences, never policy.
PARAM_DEFAULTS = {
    "include": ["session", "resources", "host", "backups"],
    "backup_limit": 10,
    "destination": None,
    "enter": True,
    "action": "unlock",
    "value": None,
    "public_key_path": None,
    "source_backup": None,
    "partition": None,
    "plan_id": None,
    "confirmation": None,
    "directory": None,
}


def default_root() -> Path:
    return Path(os.environ.get("A7M4_TOOLKIT_ROOT") or Path(__file__).resolve().parents[3]).resolve()


def find_python(root: Path) -> str | None:
    candidates = [root / ".venv/bin/python", root / "venv/bin/python"]
    for name in ("python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        probe = subprocess.run([str(candidate), "-c",
                                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
                               capture_output=True)
        if probe.returncode == 0:
            return str(candidate)
    return None


class AgentClient:
    """One `a7m4 agent` process; newline-delimited JSON-RPC over stdio."""

    def __init__(self, root: Path, python: str, on_event, on_exit):
        self.root = Path(root)
        self.python = python
        self.on_event = on_event          # (kind, payload) with kind reply|progress|error
        self.on_exit = on_exit
        self.process: subprocess.Popen | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, demo: bool = False, extra_env: dict | None = None) -> bool:
        if self.running:
            return False
        argv = [self.python, "-B", "-m", "a7m4"]
        if demo:
            argv.append("--demo")
        argv.append("agent")
        env = dict(os.environ)
        env.update(extra_env or {})
        try:
            self.process = subprocess.Popen(argv, cwd=str(self.root), stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, bufsize=1, env=env)
        except OSError as error:
            self.on_event("error", f"cannot start the agent: {error}")
            return False
        self._next_id = 0
        threading.Thread(target=self._pump, args=(self.process,), daemon=True).start()
        return True

    def _pump(self, process: subprocess.Popen) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        self.on_event("error", f"non-JSON on stdout: {line[:200]}")
                        continue
                    if "id" in message:
                        self.on_event("reply", message)
                    else:
                        self.on_event("progress", message.get("params", message))
        finally:
            code = process.wait()
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            self.process = None
            self.on_exit(code)

    def call(self, method: str, params: dict | None = None) -> int | None:
        if not self.running or self.process is None or self.process.stdin is None:
            return None
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError):
            return None
        return request_id

    def stop(self) -> bool:
        if not self.running or self.process is None:
            return False
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        return True


class Window:
    def __init__(self, root_dir: Path, python: str | None = None):
        self.root_dir = root_dir
        self.python = python or find_python(root_dir)
        self.events: queue.Queue = queue.Queue()
        self.methods: dict = {}
        self.safety_levels: dict = {}
        self.templates: dict[str, dict] = {}
        self.buttons: list[ttk.Button] = []
        self.selected: str | None = None
        self.replies: list[dict] = []

        self.tk = tk.Tk()
        try:
            self.tk.call("tk", "appname", TITLE)
        except tk.TclError:
            pass
        self.tk.title(TITLE)
        self.tk.geometry("1100x720")
        self.tk.minsize(940, 600)
        # Tk variables need a default root, so they come after `Tk()`.
        self.demo = tk.BooleanVar(value=False)
        self.palette = DARK if detect_dark() else LIGHT
        self._apply_palette()

        self.client = AgentClient(root_dir,
                                  self.python or sys.executable,
                                  lambda kind, payload: self.events.put((kind, payload)),
                                  lambda code: self.events.put(("exit", code)))
        self._build()
        self.tk.after(50, self._drain)
        self.tk.after(100, self._start_session)

    # ---------------------------------------------------------------- palette
    def _apply_palette(self) -> None:
        """One look for every widget, whatever the system appearance is.

        The aqua ttk theme ignores most colour options, so `clam` is used: it is
        less native but it is consistent, which is the point here.
        """
        p = self.palette
        style = ttk.Style(self.tk)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        for name, options in (
                ("TFrame", {"background": p["window"]}),
                ("TLabel", {"background": p["window"], "foreground": p["text"]}),
                ("TLabelframe", {"background": p["window"], "bordercolor": p["border"],
                                 "relief": "solid", "borderwidth": 1}),
                ("TLabelframe.Label", {"background": p["window"], "foreground": p["text"]}),
                ("TButton", {"background": p["panel"], "foreground": p["text"],
                             "bordercolor": p["border"], "focuscolor": p["border"]}),
                ("TCheckbutton", {"background": p["window"], "foreground": p["text"]}),
                ("TEntry", {"fieldbackground": p["field"], "foreground": p["text"],
                            "bordercolor": p["border"]})):
            style.configure(name, **options)
        style.map("TButton", background=[("active", p["border"])])
        self.tk.configure(background=p["window"])

    def safety_colour(self, level: str) -> str:
        return self.palette[SAFETY_KEY.get(level, "muted")]

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        outer = ttk.Frame(self.tk, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Sony A7M4", font=("Helvetica", 14, "bold")).pack(side="left")
        self.summary = ttk.Label(header, text="正在连接接口…", foreground=self.palette["muted"])
        self.summary.pack(side="left", padx=12)
        self.demo_check = ttk.Checkbutton(header, text="演示模式（合成相机）", variable=self.demo,
                                          command=self._restart_session)
        self.demo_check.pack(side="right")
        ttk.Button(header, text="刷新状态", command=self._refresh_status).pack(side="right", padx=6)

        columns = ttk.Frame(outer)
        columns.pack(fill="both", expand=True, pady=(10, 0))

        self.left = ttk.Frame(columns, width=210)
        self.left.pack(side="left", fill="y")
        right = ttk.Frame(columns)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        ttk.Label(right, text="参数（就是 agent 会发的那段 JSON，可改）").pack(anchor="w")
        self.params = tk.Text(right, height=7, wrap="none", background=self.palette["field"],
                              foreground=self.palette["text"], insertbackground=self.palette["text"],
                              font=("Menlo", 11), borderwidth=1, relief="solid")
        self.params.pack(fill="x", pady=(2, 6))
        self.params.insert("1.0", "{}")

        row = ttk.Frame(right)
        row.pack(fill="x")
        self.run_button = ttk.Button(row, text="执行", width=10, command=self._execute)
        self.run_button.pack(side="left")
        self.abort_button = ttk.Button(row, text="中止并重开会话", width=18, command=self._abort)
        self.abort_button.pack(side="left", padx=6)
        self.method_label = ttk.Label(row, text="未选择方法", foreground=self.palette["muted"])
        self.method_label.pack(side="left", padx=8)

        ttk.Label(right, text="日志（与 agent 收到的完全一致）").pack(anchor="w", pady=(10, 0))
        self.log = tk.Text(right, wrap="word", background=self.palette["log_bg"],
                           foreground=self.palette["log_fg"], insertbackground=self.palette["log_fg"],
                           font=("Menlo", 11), borderwidth=0, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, pady=(2, 0))
        self.log.configure(state="disabled")
        for tag, key in (("ok", "ok"), ("bad", "bad"), ("app", "accent"),
                         ("you", "you"), ("muted", "progress")):
            self.log.tag_configure(tag, foreground=self.palette[key])

        ttk.Label(outer, foreground=self.palette["hint"], justify="left",
                  text=("窗口与 agent 用同一套方法、同一套守卫：方法自带安全级别，写相机需要本次会话的完整备份（服务端强制）。\n"
                        "长操作进行中无法并行发请求（单线程会话），要停下就按「中止并重开会话」。")
                  ).pack(anchor="w", pady=(10, 0))

    def _build_method_buttons(self) -> None:
        for child in self.left.winfo_children():
            child.destroy()
        self.buttons = []
        grouped: dict[str, list[str]] = {}
        for name, spec in self.methods.items():
            grouped.setdefault(spec.get("safety", "read"), []).append(name)
        for level in SAFETY_ORDER:
            names = sorted(grouped.get(level, []))
            if not names:
                continue
            box = ttk.LabelFrame(self.left, text=SAFETY_LABEL.get(level, level), padding=8)
            box.pack(fill="x", pady=3)
            for name in names:
                text = method_label(name) + (" !" if level in ("session", "write") else "")
                button = ttk.Button(box, text=text, width=24,
                                    command=lambda n=name: self._select(n))
                button.pack(anchor="w", pady=1)
                self.buttons.append(button)
        ttk.Label(self.left, text="! = 会碰相机", foreground=self.safety_colour("write")).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------- templates
    def template(self, method: str) -> dict:
        if method not in self.templates:
            params = (self.methods.get(method, {}).get("params") or {})
            self.templates[method] = {key: PARAM_DEFAULTS[key] for key in params if key in PARAM_DEFAULTS}
        return self.templates[method]

    # --------------------------------------------------------------- session
    def _start_session(self) -> None:
        if not self.python:
            self._log("找不到 Python 3.11+，无法启动接口进程。", "bad")
            return
        if not self.client.start(demo=bool(self.demo.get())):
            self._log("接口进程启动失败。", "bad")
            return
        self._log(f"已启动接口进程：{Path(self.python).name} -m a7m4 agent"
                  f"{'（演示模式）' if self.demo.get() else ''}", "app")
        self.client.call("capabilities", {})
        self.tk.after(2000, self._poll_status)

    def _restart_session(self) -> None:
        if self.client.running:
            self.client.stop()
        self._log("—— 重开会话（上一次的写权限随会话结束而失效）——", "app")
        self.summary.configure(text="正在连接接口…", foreground=self.palette["muted"])
        self.tk.after(200, self._start_session)

    def _abort(self) -> None:
        if self.client.stop():
            self._log("—— 已中止当前操作；写权限随会话失效 ——", "bad")
        self.tk.after(200, self._restart_session)

    def _poll_status(self) -> None:
        if self.client.running:
            self._refresh_status()
            self.tk.after(3000, self._poll_status)

    def _refresh_status(self) -> None:
        self.client.call("status", {"include": ["session", "device", "host", "backups"], "backup_limit": 3})

    # ---------------------------------------------------------------- actions
    def _select(self, method: str) -> None:
        self.selected = method
        self.method_label.configure(text=f"{method_label(method)} · {method}")
        self.params.delete("1.0", "end")
        self.params.insert("1.0", json.dumps(self.template(method), ensure_ascii=False, indent=2))

    def _execute(self) -> None:
        if not self.selected:
            self._log("先在左侧选一个方法。", "bad")
            return
        method = self.selected
        raw = self.params.get("1.0", "end").strip() or "{}"
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as error:
            self._log(f"参数不是合法 JSON：{error}", "bad")
            return
        if not isinstance(params, dict):
            self._log("参数必须是一个 JSON 对象。", "bad")
            return
        if self.methods.get(method, {}).get("safety") == "write" and not messagebox.askyesno(
                TITLE, f"{method} 会写入相机。\n本次会话的完整备份已经做过了吗？"):
            return
        self._log(f"→ {method_label(method)}（{method}） {json.dumps(params, ensure_ascii=False)}", "you")
        if self.client.call(method, params) is None:
            self._log("接口进程没有在运行。", "bad")

    # ----------------------------------------------------------------- events
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "reply":
                    self.replies.append(payload)
                    self._render_reply(payload)
                elif kind == "progress":
                    self._log(f"· {json.dumps(payload, ensure_ascii=False)}", "muted")
                elif kind == "error":
                    self._log(str(payload), "bad")
                elif kind == "exit":
                    self._log(f"接口进程结束（退出码 {payload}）", "app")
                    self.summary.configure(text="接口进程已结束", foreground=self.palette["bad"])
        except queue.Empty:
            pass
        self.tk.after(50, self._drain)

    def _render_reply(self, message: dict) -> None:
        if "error" in message:
            error = message["error"]
            data = error.get("data") or {}
            hint = data.get("hint") or ""
            code = data.get("code", "?")
            self._log(f"x {error_label(code)}（{code}） {error.get('message')}"
                      f"{('  — ' + hint) if hint else ''}", "bad")
            return
        result = message.get("result")
        if isinstance(result, dict) and "methods" in result:
            self.methods = result["methods"]
            self.safety_levels = result.get("safety_levels", {})
            self._build_method_buttons()
            self._log(f"能力表：{len(self.methods)} 个方法", "ok")
            return
        if isinstance(result, dict) and "sections" in result and "state" in result:
            self._render_status(result)
            return
        self._log("ok " + json.dumps(result, ensure_ascii=False)[:4000], "ok")
        if isinstance(result, dict) and result.get("plan_id"):
            self.templates["apply"] = {"plan_id": result.get("plan_id"),
                                       "confirmation": result.get("confirmation")}
            self._log("已把 plan_id 与确认串填进 apply 的参数模板（确认串必须原样回传）", "app")

    def _render_status(self, snapshot: dict) -> None:
        session = snapshot.get("session", {})
        resources = snapshot.get("resources", {})
        device = snapshot.get("device", {})
        backups = snapshot.get("backups", {})
        parts = [f"状态 {state_label(str(snapshot.get('state')))}",
                 f"写权限 {'开' if snapshot.get('write_enabled') else '关'}"]
        if resources:
            parts.append(f"资源 {'就绪' if resources.get('ready') else '异常'}")
        if device:
            parts.append(f"设备 {'在线' if device.get('payload_usb_present') else '无'}")
        if backups:
            parts.append(f"备份 {backups.get('count')} 份")
        if session.get("backup_folder"):
            parts.append("会话已备份")
        colour = self.palette["ok"] if snapshot.get("write_enabled") else self.palette["muted"]
        self.summary.configure(text=" · ".join(parts), foreground=colour)

    def _log(self, line: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self, selftest: bool = False) -> int:
        if selftest:
            self.tk.after(2500, self.tk.destroy)
        try:
            self.tk.mainloop()
        except tk.TclError:
            pass
        finally:
            if self.client.running:
                self.client.stop()
        return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print-root" in argv:
        print(default_root())
        return 0
    return Window(default_root()).run(selftest="--selftest" in argv)


if __name__ == "__main__":
    raise SystemExit(main())

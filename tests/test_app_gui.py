"""The window is an interface client, so these tests drive the interface.

Two things matter: the protocol plumbing (`AgentClient`, no Tk needed) and the
live window path, which is where a mistake such as touching Tk from a worker
thread would show up. The previous "fake terminal" design is gone; if it came
back, `test_gui_source_is_a_client_not_a_terminal` would notice.
"""
from __future__ import annotations

import importlib.util
import re
import time
from pathlib import Path
from tkinter import ttk, TclError

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "advanced-macos/gui/app.py"


def load_gui():
    spec = importlib.util.spec_from_file_location("a7m4_gui_under_test", GUI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gui = load_gui()


def make_client(tmp_path):
    events: list = []
    exits: list = []
    client = gui.AgentClient(ROOT, gui.find_python(ROOT) or "python3",
                             lambda kind, payload: events.append((kind, payload)),
                             lambda code: exits.append(code))
    assert client.start(demo=True, extra_env={"A7M4_DATA_DIR": str(tmp_path / "state")})
    return client, events, exits


def wait_for(events, predicate, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for kind, payload in list(events):
            if kind == "reply" and predicate(payload):
                return payload
        time.sleep(0.05)
    raise AssertionError(f"no matching reply; saw {[p.get('id') for _, p in events]}")


def test_client_discovers_the_catalog_and_reads_state(tmp_path):
    client, events, _ = make_client(tmp_path)
    try:
        assert client.call("capabilities", {}) == 1
        catalog = wait_for(events, lambda m: "methods" in (m.get("result") or {}))["result"]
        assert "status" in catalog["methods"] and catalog["arbitrary_commands"] is False
        assert set(catalog["safety_levels"]) == {"read", "session", "write", "key"}
        assert all("safety" in spec for spec in catalog["methods"].values())

        assert client.call("status", {"include": ["session", "host"]}) == 2
        snapshot = wait_for(events, lambda m: (m.get("result") or {}).get("state") is not None)["result"]
        assert snapshot["state"] == "disconnected"
        assert snapshot["write_enabled"] is False
        assert snapshot["host"]["platform"] == "macos"
    finally:
        client.stop()


def test_client_surfaces_error_codes_instead_of_raising(tmp_path):
    client, events, _ = make_client(tmp_path)
    try:
        client.call("status", {"include": ["nonsense"]})
        reply = wait_for(events, lambda m: "error" in m)
        assert reply["error"]["data"]["code"] == "PARAMS"
        client.call("not_a_method", {})
        reply = wait_for(events, lambda m: (m.get("error") or {}).get("data", {}).get("code") == "METHOD")
        assert reply["error"]["data"]["code"] == "METHOD"
    finally:
        client.stop()


def test_calling_a_stopped_client_is_refused(tmp_path):
    client, _, _ = make_client(tmp_path)
    client.stop()
    assert client.running is False
    assert client.call("status", {}) is None
    assert client.stop() is False


def test_window_drives_the_interface_through_its_own_event_loop(tmp_path):
    """The live path: worker threads must reach the window via the queue."""
    try:
        window = gui.Window(ROOT, python=gui.find_python(ROOT))
    except TclError as error:                       # no GUI session
        import pytest as _pytest
        _pytest.skip(f"no GUI session: {error}")
    original_start = gui.AgentClient.start

    def isolated_start(self, demo=False, extra_env=None):
        return original_start(self, demo=demo, extra_env={"A7M4_DATA_DIR": str(tmp_path / "state")})

    gui.AgentClient.start = isolated_start
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            window.tk.update()
            if window.methods and "状态" in window.summary.cget("text"):
                break
            time.sleep(0.05)
        assert window.methods, "capabilities must populate the method list"
        assert "能力表" in window.log.get("1.0", "end")
        assert "状态" in window.summary.cget("text"), window.summary.cget("text")
        # One button per method, grouped by the catalog's own safety levels.
        assert len(window.buttons) == len(window.methods), (len(window.buttons), len(window.methods))
        assert window.params.get("1.0", "end").strip() == "{}"
    finally:
        gui.AgentClient.start = original_start
        window.tk.destroy()
        if window.client.running:
            window.client.stop()


def test_gui_source_is_a_client_not_a_terminal():
    source = GUI.read_text("utf8")
    compile(source, str(GUI), "exec")
    assert "class AgentClient" in source, "the window must speak the JSON-RPC interface"
    assert '"open", "-a", "Terminal"' not in source, "no terminal may be spawned"
    assert "A7M4_NO_PAUSE" not in source, "the fake-terminal machinery must stay gone"


def _palette_literals(source: str) -> set[str]:
    """Hex colours that appear inside the LIGHT / DARK palette blocks."""
    found: set[str] = set()
    for name in ("LIGHT = {", "DARK = {"):
        start = source.index(name)
        end = source.index("\n}", start)
        found.update(re.findall(r"#[0-9a-fA-F]{6}", source[start:end]))
    return found


def test_every_colour_comes_from_the_palette():
    """No stray literals: mixing Tk defaults with hand-picked colours is exactly
    how the window ended up with unreadable text in dark mode."""
    source = GUI.read_text("utf8")
    used = set(re.findall(r"#[0-9a-fA-F]{6}", source))
    declared = _palette_literals(source)
    assert len(declared) > 20, "the palettes should define the whole look"
    stray = sorted(used - declared)
    assert stray == [], f"colours outside the palette: {stray}"


def test_window_applies_one_palette_to_its_widgets(tmp_path):
    try:
        window = gui.Window(ROOT, python=gui.find_python(ROOT))
    except TclError as error:                       # no GUI session
        import pytest as _pytest
        _pytest.skip(f"no GUI session: {error}")
    try:
        palette = window.palette
        assert palette is (gui.DARK if gui.detect_dark() else gui.LIGHT)
        style = ttk.Style(window.tk)
        assert style.theme_use() == "clam", "aqua ignores colour configuration"
        # The two text widgets and the frames all come from the same palette.
        assert window.log.cget("background") == palette["log_bg"]
        assert window.log.cget("foreground") == palette["log_fg"]
        assert window.params.cget("background") == palette["field"]
        assert window.params.cget("foreground") == palette["text"]
        assert str(window.tk.cget("background")) == palette["window"]
        assert style.lookup("TLabel", "background") == palette["window"]
        assert style.lookup("TLabel", "foreground") == palette["text"]
        # Safety colours resolve through the palette, not through literals.
        assert window.safety_colour("write") == palette["bad"]
        assert window.safety_colour("read") == palette["ok"]
        for key in ("window", "panel", "field", "border", "text", "muted", "hint",
                    "log_bg", "log_fg", "ok", "bad", "warn", "key", "accent", "you", "progress"):
            assert key in palette and key in gui.LIGHT and key in gui.DARK, key
    finally:
        window.tk.destroy()
        if window.client.running:
            window.client.stop()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def test_every_advertised_method_has_a_chinese_label(tmp_path):
    """The window must not show bare English method ids.

    The id stays the contract, but every method the catalog advertises needs a
    Chinese display name, or a new method would silently appear in English.
    """
    client, events, _ = make_client(tmp_path)
    try:
        client.call("capabilities", {})
        catalog = wait_for(events, lambda m: "methods" in (m.get("result") or {}))["result"]
        missing = sorted(set(catalog["methods"]) - set(gui.METHOD_LABELS))
        assert missing == [], f"methods without a Chinese label: {missing}"
        for name in catalog["methods"]:
            label = gui.method_label(name)
            assert _has_cjk(label), f"{name} -> {label!r}"
            assert name not in label or name == label, f"{name} should show a name, not the id"
    finally:
        client.stop()


def test_state_and_error_labels_are_chinese_with_a_raw_fallback():
    for value in ("disconnected", "connecting", "backing_up", "ready", "busy"):
        assert _has_cjk(gui.state_label(value)), value
    for code in ("PARAMS", "METHOD", "BACKUP_REQUIRED", "BUSY", "LOCK", "LOCK_LOST",
                 "ASSETS_MISSING", "CONFIRMATION"):
        assert _has_cjk(gui.error_label(code)), code
    # An unknown value must still be shown, not swallowed.
    assert gui.state_label("something_new") == "something_new"
    assert gui.error_label("BRAND_NEW") == "BRAND_NEW"
    assert gui.method_label("future_method") == "future_method"


def test_buttons_show_chinese_labels_not_ids(tmp_path):
    try:
        window = gui.Window(ROOT, python=gui.find_python(ROOT))
    except TclError as error:
        import pytest as _pytest
        _pytest.skip(f"no GUI session: {error}")
    original_start = gui.AgentClient.start

    def isolated_start(self, demo=False, extra_env=None):
        return original_start(self, demo=demo, extra_env={"A7M4_DATA_DIR": str(tmp_path / "state")})

    gui.AgentClient.start = isolated_start
    try:
        deadline = time.time() + 40
        while time.time() < deadline and not window.methods:
            window.tk.update()
            time.sleep(0.05)
        assert window.methods
        texts = [button.cget("text") for button in window.buttons]
        assert len(texts) == len(window.methods)
        for text in texts:
            assert _has_cjk(str(text)), f"button shows no Chinese: {text!r}"
    finally:
        gui.AgentClient.start = original_start
        window.tk.destroy()
        if window.client.running:
            window.client.stop()

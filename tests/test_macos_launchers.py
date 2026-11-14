"""macOS entry points: argument forwarding, exit codes, demo refusal.

These run the real `.command` launchers in a temporary copy of the tree with a
stub `a7m4` package, so nothing here can reach a camera, a serial port or a
fastboot binary.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = "/bin/bash"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS .command entry points")


def copy_script(name, root):
    """Copy one launcher and keep its execute bit (copyfile does not)."""
    target = root / "advanced-macos" / name
    shutil.copyfile(ROOT / "advanced-macos" / name, target)
    target.chmod(0o755)
    return target


def launcher_tree(tmp_path):
    root = tmp_path / "port copy & 中文"
    (root / "advanced-macos").mkdir(parents=True)
    (root / "a7m4").mkdir()
    shutil.copyfile(ROOT / "advanced-macos/_launch.sh", root / "advanced-macos/_launch.sh")
    (root / "advanced-macos/_launch.sh").chmod(0o755)
    (root / "a7m4/__init__.py").write_text("", "utf8")
    (root / "a7m4/recover_sd.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path('args.json').write_text(json.dumps(sys.argv[1:]), 'utf8')\nraise SystemExit(7)\n", "utf8")
    (root / "a7m4/__main__.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path('args.json').write_text(json.dumps(sys.argv[1:]), 'utf8')\nraise SystemExit(7)\n", "utf8")
    return root


def run(script, root, env_extra=None):
    env = os.environ | {"A7M4_PYTHON": sys.executable, "A7M4_NO_PAUSE": "1", "A7M4_DEMO": "0"}
    env |= env_extra or {}
    return subprocess.run([SHELL, str(script)], cwd=root / "advanced-macos",
                          capture_output=True, encoding="utf8", env=env, timeout=60)


@pytest.mark.parametrize("name,expected", [
    ("一键救砖_从SD卡恢复.command", []),
    ("只检查fastboot连接.command", ["--check-connection"]),
])
def test_recovery_launchers_forward_arguments_and_exit_status(tmp_path, name, expected):
    root = launcher_tree(tmp_path)
    script = copy_script(name, root)
    result = run(script, root)
    assert result.returncode == 7, result.stdout + result.stderr
    assert json.loads((root / "args.json").read_text("utf8")) == expected
    assert "Exit code: 7" in result.stdout


def test_maintenance_launcher_passes_demo_flag(tmp_path):
    root = launcher_tree(tmp_path)
    script = copy_script("查看或设置固件版本.command", root)
    result = run(script, root, {"A7M4_DEMO": "1"})
    assert result.returncode == 7, result.stdout + result.stderr
    assert json.loads((root / "args.json").read_text("utf8")) == ["--demo", "maintain", "--action", "set_version"]


def test_recovery_refuses_demo_flag_instead_of_operating_hardware(tmp_path):
    root = launcher_tree(tmp_path)
    script = copy_script("一键救砖_从SD卡恢复.command", root)
    result = run(script, root, {"A7M4_DEMO": "1"})
    assert result.returncode == 2
    assert "Recovery does not use A7M4_DEMO" in result.stdout + result.stderr
    assert not (root / "args.json").exists()


def test_launcher_reports_missing_python_without_running_anything(tmp_path):
    """The missing-runtime branch must be reached, not merely tolerated.

    An earlier version asserted `returncode in (2, 7)` and pointed PATH at
    /usr/bin, so the fallback found the system python3, ran the stub, and the
    test passed without ever exercising the branch it claimed to cover. PATH is
    isolated here so only the reported-missing path can satisfy the assertion.
    """
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    for tool in ("dirname", "command"):          # _launch.sh needs these to run at all
        source = shutil.which(tool)
        if source:
            (bin_dir / tool).symlink_to(source)
    root = launcher_tree(tmp_path)
    script = copy_script("检查运行环境.command", root)
    env = {"PATH": str(bin_dir), "A7M4_PYTHON": str(tmp_path / "missing-python"), "A7M4_NO_PAUSE": "1"}
    result = subprocess.run([SHELL, str(script)],
                            cwd=root / "advanced-macos", capture_output=True, encoding="utf8",
                            env=os.environ | env, timeout=60)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Python 3.11+ not found" in (result.stdout + result.stderr)
    assert not (root / "args.json").exists(), "the stub must not have run"


def test_launcher_refuses_an_interpreter_older_than_the_requirement(tmp_path):
    """A `python3` that exists but is too old must be rejected up front.

    `requires-python` is >=3.11 and a stock macOS python3 is often 3.9/3.10; it
    used to be accepted and then fail deep inside the toolkit with something
    unrelated (`hashlib.file_digest`). The launcher now probes the version.
    """
    bin_dir = tmp_path / "old-bin"
    bin_dir.mkdir()
    for tool in ("dirname", "command"):
        source = shutil.which(tool)
        if source:
            (bin_dir / tool).symlink_to(source)
    too_old = bin_dir / "python3"
    too_old.write_text("#!/bin/bash\nexit 1\n", "utf8")   # fails the version probe
    too_old.chmod(0o755)
    root = launcher_tree(tmp_path)
    script = copy_script("检查运行环境.command", root)
    env = {"PATH": str(bin_dir), "A7M4_PYTHON": "", "A7M4_NO_PAUSE": "1"}
    result = subprocess.run([SHELL, str(script)],
                            cwd=root / "advanced-macos", capture_output=True, encoding="utf8",
                            env=os.environ | env, timeout=60)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Python 3.11+ not found" in (result.stdout + result.stderr)
    assert not (root / "args.json").exists(), "an unusable interpreter must not run the toolkit"


def test_command_files_forward_their_arguments(tmp_path):
    """A .command must not silently drop arguments passed on the command line."""
    for script in sorted((ROOT / "advanced-macos").glob("*.command")):
        text = script.read_text("utf8")
        assert '"$@"' in text, f"{script.name} drops its arguments"


def test_every_command_file_has_a_matching_launcher():
    for script in sorted((ROOT / "advanced-macos").glob("*.command")):
        text = script.read_text("utf8")
        assert text.startswith("#!/bin/bash"), script.name
        assert '_launch.sh' in text, script.name
        assert os.access(script, os.X_OK), script.name
    assert os.access(ROOT / "advanced-macos/_launch.sh", os.X_OK)


def test_app_bundle_builds_with_a_working_gui_script(tmp_path):
    """The launcher bundle must stay buildable and its GUI must import.

    The bundle is generated, so a broken generator would silently ship a
    non-starting app. This rebuilds into a temp directory and checks the parts
    that make it startable: the wrapper is executable, the entry points are, the
    icon is a real .icns, and the GUI script compiles.
    """
    python = shutil.which("python3.12") or shutil.which("python3.11") or sys.executable
    result = subprocess.run([python, "tools/make_app_macos.py", "--destination", str(tmp_path)],
                            cwd=ROOT, capture_output=True, encoding="utf8", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    app = tmp_path / "A7M4 ToolKit.app"
    wrapper = app / "Contents/MacOS/launch"
    assert wrapper.is_file() and os.access(wrapper, os.X_OK), "the bundle executable must be runnable"
    gui = app / "Contents/Resources/app.py"
    compile(gui.read_text("utf8"), str(gui), "exec")          # syntax only, no GUI session needed
    icon = app / "Contents/Resources/icon.icns"
    assert icon.is_file() and icon.stat().st_size > 1000
    plist = (app / "Contents/Info.plist").read_text("utf8")
    assert "<key>CFBundleExecutable</key><string>launch</string>" in plist
    assert plist.count("A7M4 ToolKit") >= 1
    for entry in sorted((ROOT / "advanced-macos").glob("*.command")):
        assert os.access(entry, os.X_OK), f"{entry.name} must be executable for Terminal to run it"

"""macOS host adaptation: asset resolution, lock, volumes, driver no-op.

These tests never open a device, a serial port or a subprocess that talks to
hardware. Windows-only behaviour is asserted only where the same code path is
shared, so the suite stays meaningful on either host.
"""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import types

import pytest

from a7m4 import host
from a7m4.common import Assets, ToolError


def pack(root, files, platform=None, distribution="LOCAL-ONLY test pack"):
    """Write a synthetic resource pack; contents are never executed."""
    local = root / "local"
    records = {}
    for name, data in files.items():
        path = local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if name.rsplit("/", 1)[-1] in {"fastboot", "sqfs2tar", "tar2sqfs"}:
            path.chmod(0o755)
        records[name] = {"size": len(data), "sha256": host_sha(data)}
    manifest = {"schema": 1, "distribution": distribution, "files": records}
    if platform:
        manifest["platform"] = platform
    (root / "manifest.local.json").write_text(json.dumps(manifest), "utf8")
    return root


def host_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_logical_name_resolves_to_native_binary_on_posix(tmp_path):
    root = pack(tmp_path, {"bin/fastboot": b"native host tool", "bin/fastboot.bin": b"payload"})
    assets = Assets(root)
    if host.IS_POSIX:
        assert assets.get("bin/fastboot.exe").name == "fastboot"
    else:
        with pytest.raises(ToolError) as caught:
            assets.get("bin/fastboot.exe")
        assert caught.value.code == "ASSETS_MISSING"


def test_path_field_records_real_file_without_changing_pins(tmp_path):
    # A pack may keep the reviewed logical key and point at the real file.
    root = tmp_path
    (root / "local/bin").mkdir(parents=True)
    (root / "local/bin/fastboot").write_bytes(b"native")
    (root / "local/bin/fastboot").chmod(0o755)
    manifest = {"schema": 1, "files": {"bin/fastboot.exe": {
        "path": "bin/fastboot", "size": 6, "sha256": host_sha(b"native")}}}
    (root / "manifest.local.json").write_text(json.dumps(manifest), "utf8")
    assets = Assets(root)
    if host.IS_POSIX:
        assert assets.get("bin/fastboot.exe") == (root / "local/bin/fastboot").resolve()
    else:
        with pytest.raises(ToolError):
            assets.get("bin/fastboot.exe")


def test_modified_native_binary_is_rejected(tmp_path):
    root = pack(tmp_path, {"bin/fastboot": b"original"})
    (root / "local/bin/fastboot").write_bytes(b"tampered")
    with pytest.raises(ToolError) as caught:
        Assets(root).get("bin/fastboot.exe")
    assert caught.value.code == "ASSET_INTEGRITY"


def test_non_executable_native_binary_is_rejected(tmp_path):
    if not host.IS_POSIX:
        pytest.skip("POSIX execute-bit check")
    root = pack(tmp_path, {"bin/fastboot": b"original"})
    (root / "local/bin/fastboot").chmod(0o644)
    with pytest.raises(ToolError) as caught:
        Assets(root).get("bin/fastboot.exe")
    # Permission, not tampering: the content hash still matches here.
    assert caught.value.code == "ASSET_PERMISSION"


def test_execute_bit_follows_the_logical_name_not_the_suffix(tmp_path):
    """An executable with an extension must not slip past the execute-bit check."""
    if not host.IS_POSIX:
        pytest.skip("POSIX execute-bit check")
    root = tmp_path
    (root / "local/squashfs").mkdir(parents=True)
    (root / "local/squashfs/sqfs2tar").write_bytes(b"tool")
    (root / "local/squashfs/sqfs2tar").chmod(0o644)
    manifest = {"schema": 1, "files": {"squashfs/sqfs2tar.exe": {
        "path": "squashfs/sqfs2tar", "size": 4, "sha256": host_sha(b"tool")}}}
    (root / "manifest.local.json").write_text(json.dumps(manifest), "utf8")
    with pytest.raises(ToolError) as caught:
        Assets(root).get("squashfs/sqfs2tar.exe")
    assert caught.value.code == "ASSET_PERMISSION"


def test_native_name_request_is_gated_like_the_logical_name(tmp_path):
    """Asking for `bin/fastboot` directly must not skip the execute-bit gate.

    Regression: the gate was keyed on the logical `.exe` names only, so a caller
    that used the native spelling bypassed the +x requirement. The gate now
    covers the request name and every candidate it aliases to.
    """
    if not host.IS_POSIX:
        pytest.skip("POSIX execute-bit check")
    assert host.is_executable_asset("bin/fastboot"), "native name counts as executable"
    assert host.is_executable_asset("bin/fastboot.exe"), "logical name counts as executable"
    assert not host.is_executable_asset("bin/fastboot.bin"), "payload data is not an executable"
    root = pack(tmp_path, {"bin/fastboot": b"f"})
    (root / "local/bin/fastboot").chmod(0o644)
    with pytest.raises(ToolError) as caught:
        Assets(root).get("bin/fastboot")
    assert caught.value.code == "ASSET_PERMISSION"


def test_data_resources_are_not_required_to_be_executable(tmp_path):
    """A `.bin` payload is read, never executed; a missing +x must not matter."""
    if not host.IS_POSIX:
        pytest.skip("POSIX execute-bit check")
    root = pack(tmp_path, {"bin/fastboot": b"f", "bin/fastboot.bin": b"payload"})
    (root / "local/bin/fastboot.bin").chmod(0o644)
    assert Assets(root).get("bin/fastboot.bin").name == "fastboot.bin"


def test_required_asset_set_follows_host(tmp_path):
    native = {"bin/fastboot": b"f", "bin/fastboot.bin": b"p", "bin/uart_boot.ino.uf2": b"u",
              "squashfs/sqfs2tar": b"s", "squashfs/tar2sqfs": b"t"}
    root = pack(tmp_path, native)
    status = Assets(root).check()
    if host.IS_POSIX:
        assert status["ready"], status["errors"]
        assert status["platform"] == "macos"
    else:
        # The Windows pack additionally needs the driver INF.
        assert not status["ready"] and any("android_winusb" in e for e in status["errors"])


def test_doctor_reports_platform(tmp_path):
    root = pack(tmp_path, {"bin/fastboot": b"f"})
    assert Assets(root).check()["platform"] == host.PLATFORM_LABEL


def test_resources_env_override(tmp_path, monkeypatch):
    root = pack(tmp_path, {"bin/fastboot": b"f"})
    monkeypatch.setenv("A7M4_RESOURCES", str(root))
    assert Assets().base == root


def test_data_dir_override_and_macos_default(tmp_path, monkeypatch):
    monkeypatch.setenv("A7M4_DATA_DIR", str(tmp_path / "state"))
    assert host.data_dir() == tmp_path / "state"
    monkeypatch.delenv("A7M4_DATA_DIR")
    if host.IS_MACOS:
        assert host.data_dir() == Path.home() / "Library" / "Application Support" / "A7M4Toolkit"


def test_bootsel_volume_detection_reads_info_file(tmp_path):
    board = tmp_path / "RPI-RP2"
    board.mkdir()
    (board / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\nModel: RP2040\n", "utf8")
    other = tmp_path / "Untitled"
    other.mkdir()
    (other / "INFO_UF2.TXT").write_text("Board-ID: other\n", "utf8")
    found = host.bootsel_volumes(tmp_path)
    if host.IS_WINDOWS:
        assert found == []          # drive-letter enumeration, not this root
    else:
        assert found == [board]


def test_lock_excludes_a_second_holder(tmp_path):
    lock = tmp_path / "device-lock"
    with host.hardware_lock(lock):
        with pytest.raises(ToolError) as caught:
            with host.hardware_lock(lock):
                pass
        assert caught.value.code == "BUSY"
    # Released: a later acquisition succeeds.
    with host.hardware_lock(lock):
        pass


def test_lock_marker_makes_plain_rmdir_fail(tmp_path):
    """An empty directory can be rmdir'd while flocked, which would drop exclusion."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    lock = tmp_path / "device-lock"
    with host.hardware_lock(lock):
        assert (lock / host.LOCK_MARKER).exists(), "a marker keeps the directory non-empty"
        with pytest.raises(OSError):
            os.rmdir(lock)
        with pytest.raises(ToolError) as caught:
            with host.hardware_lock(lock):
                pass
        assert caught.value.code == "BUSY"


def test_a_removed_lock_is_detected_before_the_next_write(tmp_path):
    """`rm -rf` still defeats the marker, so the write path re-validates the lock."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    lock = tmp_path / "device-lock"
    with host.hardware_lock(lock):
        host.verify_device_lock()          # intact: no error
        shutil.rmtree(lock)
        with pytest.raises(ToolError) as caught:
            host.verify_device_lock()
        assert caught.value.code == "LOCK_LOST"


def test_a_replaced_lock_is_detected(tmp_path):
    """Recreating the path yields a different inode: same failure, named separately."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    lock = tmp_path / "device-lock"
    with host.hardware_lock(lock):
        shutil.rmtree(lock)
        (lock).mkdir()
        with pytest.raises(ToolError) as caught:
            host.verify_device_lock()
        assert caught.value.code == "LOCK_LOST"


def test_write_without_a_held_lock_is_refused():
    """A write path that skipped hardware_lock must not command the camera."""
    assert host.active_locks() == [], "no prior test may leave a lock registered"
    with pytest.raises(ToolError) as caught:
        host.verify_device_lock()
    assert caught.value.code == "LOCK"


def test_a_thread_that_never_locked_cannot_borrow_another_threads_authority(tmp_path):
    """Regression: the verifier is per-thread, not process-global.

    A process-global stack let a thread that never acquired anything pass
    `verify_device_lock()` as long as some other thread held a lock — fail-open,
    and the exact opposite of what the guard is for. Two independent reviews
    reproduced it; this pins the fix.
    """
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    directory = tmp_path / "device-lock"
    outcome = {}

    def borrower():
        # This thread holds nothing.
        try:
            host.verify_device_lock()
            outcome["result"] = "PASSED"
        except ToolError as error:
            outcome["result"] = error.code

    with host.hardware_lock(directory):
        thread = threading.Thread(target=borrower)
        thread.start()
        thread.join(timeout=10)
    assert outcome.get("result") == "LOCK", "borrowing another thread's lock must fail closed"


def test_nested_locks_unwind_to_the_callers_own_lock(tmp_path):
    """Same-thread nesting must verify the innermost lock the caller itself holds."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    outer = tmp_path / "outer"
    inner = tmp_path / "inner"
    with host.hardware_lock(outer):
        assert host.active_locks()[-1].target == outer.resolve()
        with host.hardware_lock(inner):
            assert len(host.active_locks()) == 2
            assert host.active_locks()[-1].target == inner.resolve()
        assert len(host.active_locks()) == 1
        assert host.active_locks()[-1].target == outer.resolve()
        # The outer holder must still verify against its own lock, not a stale entry.
        host.verify_device_lock()
    assert host.active_locks() == []


def test_a_context_closed_on_another_thread_leaves_no_registration(tmp_path):
    """Regression: the unwind must target the OWNING thread's registry.

    Resolving `active_locks()` inside the finally used whichever thread closed
    the context, so a context entered on one thread and closed on another left
    the owner's registration behind. With descriptor reuse the stale entry then
    still passed verification despite no lock being held.
    """
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    directory = tmp_path / "device-lock"
    manager = host.hardware_lock(directory)
    manager.__enter__()
    assert len(host.active_locks()) == 1
    closer = threading.Thread(target=lambda: manager.__exit__(None, None, None))
    closer.start()
    closer.join(timeout=10)
    assert host.active_locks() == [], "a cross-thread close must not strand the owner's entry"
    with pytest.raises(ToolError) as caught:
        host.verify_device_lock()
    assert caught.value.code == "LOCK"


def test_a_spent_lock_refuses_to_verify(tmp_path):
    """Object-level guard: a leaked reference to a released lock is not authority."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    directory = tmp_path / "device-lock"
    with host.hardware_lock(directory) as held:
        host.verify_device_lock()
    with pytest.raises(ToolError) as caught:
        held.verify()
    assert caught.value.code == "LOCK_LOST"


def test_a_stale_holder_cannot_validate_the_replacement_holders_lock(tmp_path):
    """Regression: verification must read the caller's own lock.

    With a shared registry and the default path this interleaving let a stale
    holder verify the *replacement* holder's inode and miss its own LOCK_LOST,
    so two ordinary writers could proceed.
    """
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    directory = tmp_path / "device-lock"
    holding = threading.Event()
    release = threading.Event()
    outcome = {}

    def replacement_holder():
        try:
            with host.hardware_lock(directory):
                holding.set()
                release.wait(timeout=10)
        except ToolError as error:
            outcome["holder"] = error.code
            holding.set()

    with host.hardware_lock(directory):          # this thread holds the original
        shutil.rmtree(directory)                 # path replaced -> new inode
        directory.mkdir()
        worker = threading.Thread(target=replacement_holder)
        worker.start()
        assert holding.wait(timeout=10), "the replacement holder should acquire"
        try:
            host.verify_device_lock()
            outcome["stale"] = "PASSED"
        except ToolError as error:
            outcome["stale"] = error.code
        release.set()
        worker.join(timeout=10)
    assert outcome.get("stale") == "LOCK_LOST", "its own lost lock must be reported, not the replacement's"


def test_an_interruption_at_any_line_leaves_no_registration(tmp_path):
    """Exhaustive injection: no line of the acquisition may strand a registration.

    This is the invariant the lock exists to provide, and it is fully under our
    control. A descriptor opened before the protected region is checked by
    `test_an_interruption_before_the_flock_does_not_leak_the_descriptor`, because
    an interrupt landing *inside* a cleanup block can legitimately skip the rest
    of that block — that is a property of asynchronous interruption, not of this
    code. Each iteration uses a fresh lock directory so a skipped cleanup cannot
    contaminate the next one.
    """
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    code = host.hardware_lock.__wrapped__.__code__
    lines = sorted({line for _, _, line in code.co_lines() if line is not None})
    assert len(lines) > 20, "expected the real generator body"

    leaks = []
    fired_lines = 0
    for index, target in enumerate(lines):
        directory = tmp_path / f"device-lock-{index}"
        fired = []

        def trace(frame, event, arg, target=target, fired=fired):
            if frame.f_code is code and event == "line" and frame.f_lineno == target:
                fired.append(target)
                raise KeyboardInterrupt(f"injected at {target}")
            return trace

        try:
            sys.settrace(trace)
            try:
                with host.hardware_lock(directory):
                    pass
            except KeyboardInterrupt:
                pass
        finally:
            sys.settrace(None)
        if not fired:
            continue          # this line is not on this platform's path
        fired_lines += 1
        # The invariant that matters: nothing may still authorise a write. A
        # survivor is tolerable only if it refuses — an interruption inside a
        # cleanup block can skip the rest of that block.
        try:
            host.verify_device_lock()
            leaks.append((target, "still authorises a write"))
        except ToolError:
            pass
        host.active_locks().clear()
    assert fired_lines > 10, f"injections must actually fire (fired {fired_lines})"
    assert leaks == [], f"interruptions left authority behind: {leaks}"


def test_an_interruption_before_the_flock_does_not_leak_the_descriptor(tmp_path):
    """Regression: the descriptor is opened INSIDE the protected region.

    An interrupt between `os.open` returning and the cleanup block being
    installed used to leak a bare descriptor, which has no finaliser to close it
    and would hold the flock for the life of the process.
    """
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    import inspect

    source_lines, start = inspect.getsourcelines(host.hardware_lock.__wrapped__)
    target = start + next(i for i, line in enumerate(source_lines) if "os.open(target" in line)
    code = host.hardware_lock.__wrapped__.__code__
    directory = tmp_path / "device-lock"

    def open_fds():
        try:
            return len(os.listdir("/dev/fd"))
        except OSError:
            return None

    baseline = open_fds()
    injected = []

    def trace(frame, event, arg):
        if frame.f_code is code and event == "line" and frame.f_lineno == target:
            injected.append(target)
            raise KeyboardInterrupt("injected at os.open")
        return trace

    try:
        sys.settrace(trace)
        try:
            with host.hardware_lock(directory):
                pass
        except KeyboardInterrupt:
            pass
    finally:
        sys.settrace(None)
    assert injected, "the injection must actually fire"
    assert host.active_locks() == []
    after = open_fds()
    if baseline is not None and after is not None:
        assert after <= baseline, f"descriptor leaked at the open boundary: {baseline} -> {after}"


def _fn(value=None):
    """A stub Win32 function: callable, with settable argtypes/restype."""
    class Fn:
        def __init__(self, ret):
            self.argtypes = None
            self.restype = None
            self._ret = ret

        def __call__(self, *a, **k):
            return self._ret() if callable(self._ret) else self._ret

    return Fn(value)


def _fake_win_dll(acquire_code=0):
    """A stub kernel32 exposing just enough for the Windows lock branch.

    acquire_code 0 is WAIT_OBJECT_0 (acquired); 0x102 is WAIT_TIMEOUT (busy).
    """
    class Fn:
        def __init__(self, ret=None):
            self.argtypes = None
            self.restype = None
            self._ret = ret

        def __call__(self, *a, **k):
            return self._ret() if callable(self._ret) else self._ret

    class Kernel:
        def __init__(self):
            self.CreateMutexW = Fn(lambda: 0x1234)
            self.WaitForSingleObject = Fn(acquire_code)
            self.ReleaseMutex = Fn(0)
            self.CloseHandle = Fn(0)

    return Kernel


@contextlib.contextmanager
def windows_mutex(acquire_code=0):
    """Run the Windows lock branch against a stub kernel32.

    `ctypes` is replaced in `sys.modules` and removed again afterwards even when
    it was never imported before — leaving the stub behind poisons every later
    test that touches the real module (an earlier version leaked it and broke
    the IOKit probe test).
    """
    stub = types.ModuleType("ctypes")
    stub.WinDLL = lambda *a, **k: _fake_win_dll(acquire_code)()
    stub.c_void_p = stub.c_bool = stub.c_wchar_p = stub.c_uint32 = object
    had_ctypes = "ctypes" in sys.modules
    saved_module = sys.modules.get("ctypes")
    saved_flag = host.IS_WINDOWS
    sys.modules["ctypes"] = stub
    host.IS_WINDOWS = True
    try:
        yield
    finally:
        host.IS_WINDOWS = saved_flag
        if had_ctypes:
            sys.modules["ctypes"] = saved_module
        else:
            sys.modules.pop("ctypes", None)


def test_windows_lock_still_lets_a_write_through():
    """Regression: the Windows branch must register the held lock.

    `verify_device_lock()` was wired into the write paths, but the Windows
    mutex branch originally yielded None without registering anything, so the
    check raised LOCK and every flash failed on Windows despite a successfully
    acquired mutex. The mutex lives in the kernel namespace and cannot be
    unlinked, so the Windows handle verifies trivially — but it must verify at
    all, not be skipped, or a caller that forgot hardware_lock would write.
    """
    with windows_mutex():
        with host.hardware_lock():
            host.verify_device_lock()          # must not raise
        assert host.active_locks() == [], "the lock must be de-registered on release"


def test_windows_busy_mutex_still_refuses():
    with windows_mutex(0x102):                 # WAIT_TIMEOUT
        with pytest.raises(ToolError) as caught:
            with host.hardware_lock():
                pass
        assert caught.value.code == "BUSY"
        assert host.active_locks() == [], "a refused acquisition must register nothing"


def test_windows_nested_acquire_unwinds_to_the_outer_holder():
    """A Win32 mutex is recursively acquirable: the stack must survive nesting.

    With a single global slot the inner release would clear the outer holder and
    the next write would be wrongly refused.
    """
    with windows_mutex():
        with host.hardware_lock() as outer:
            with host.hardware_lock() as inner:
                assert inner is not outer
                assert len(host.active_locks()) == 2
                host.verify_device_lock()
            assert len(host.active_locks()) == 1
            assert host.active_locks()[-1] is outer
            host.verify_device_lock()
        assert host.active_locks() == []
        with pytest.raises(ToolError) as caught:
            host.verify_device_lock()
        assert caught.value.code == "LOCK"


def test_windows_body_exception_releases_and_de_registers():
    with windows_mutex():
        with pytest.raises(RuntimeError):
            with host.hardware_lock():
                raise RuntimeError("boom")
        assert host.active_locks() == [], "an exception must not leak a registration"


def test_windows_handle_is_released_when_the_wrapper_cannot_be_built():
    """Regression: the acquisition and the wrapper share one protected region.

    An earlier restructure constructed the wrapper outside the cleanup block, so
    a failure there leaked an owned mutex handle — and any waiter stayed blocked
    until the owner terminated.
    """
    calls = []

    class Recording:
        def __init__(self):
            self.CreateMutexW = _fn(lambda: 0x9)
            self.WaitForSingleObject = _fn(0)          # acquired
            self.ReleaseMutex = _fn(lambda: calls.append("release"))
            self.CloseHandle = _fn(lambda: calls.append("close"))

    original_kernel_lock = host.KernelLock

    class Exploding(original_kernel_lock):
        def __init__(self, handle):
            raise RuntimeError("cannot build the wrapper")

    stub = types.ModuleType("ctypes")
    stub.WinDLL = lambda *a, **k: Recording()
    stub.c_void_p = stub.c_bool = stub.c_wchar_p = stub.c_uint32 = object
    had_ctypes = "ctypes" in sys.modules
    saved_module = sys.modules.get("ctypes")
    saved_flag = host.IS_WINDOWS
    sys.modules["ctypes"] = stub
    host.IS_WINDOWS = True
    host.KernelLock = Exploding
    try:
        with pytest.raises(RuntimeError):
            with host.hardware_lock():
                pass
    finally:
        host.KernelLock = original_kernel_lock
        host.IS_WINDOWS = saved_flag
        if had_ctypes:
            sys.modules["ctypes"] = saved_module
        else:
            sys.modules.pop("ctypes", None)
    assert "release" in calls, "the owned mutex must be released"
    assert "close" in calls, "the owned handle must be closed"
    assert host.active_locks() == []


def test_lock_refuses_a_file_target(tmp_path):
    target = tmp_path / "toolkit.lock"
    target.write_text("")
    if host.IS_WINDOWS:
        pytest.skip("POSIX directory-lock guarantee")
    with pytest.raises(ToolError) as caught:
        with host.hardware_lock(target):
            pass
    assert caught.value.code == "LOCK"


def test_lock_does_not_follow_the_data_dir_override(tmp_path, monkeypatch):
    """Two instances with different A7M4_DATA_DIR must still exclude each other."""
    if host.IS_WINDOWS:
        pytest.skip("POSIX flock path")
    monkeypatch.setenv("A7M4_DATA_DIR", str(tmp_path / "a"))
    first = host.lock_dir()
    monkeypatch.setenv("A7M4_DATA_DIR", str(tmp_path / "b"))
    second = host.lock_dir()
    assert first == second, "the device lock must not move with user state"


def test_engine_lock_is_the_host_lock():
    from a7m4 import engine, sd_recovery
    assert engine.hardware_lock is host.hardware_lock
    assert sd_recovery.hardware_lock is host.hardware_lock


def test_driver_step_is_a_verified_noop_on_macos(tmp_path):
    from a7m4 import hardware
    root = pack(tmp_path, {"bin/fastboot": b"f"})
    if not host.IS_MACOS:
        pytest.skip("macOS-specific no-op")
    events = []
    result = hardware.install_driver(Assets(root), events.append)
    assert result["driver"] == "not-required-on-macos" and result["camera_io"] is False
    assert events and events[0]["phase"] == "driver"


def test_register_resources_accepts_native_names(tmp_path):
    source = tmp_path / "resources"
    (source / "bin").mkdir(parents=True)
    (source / "squashfs").mkdir(parents=True)
    for name in ("bin/fastboot", "bin/fastboot.bin", "bin/uart_boot.ino.uf2",
                 "squashfs/sqfs2tar", "squashfs/tar2sqfs"):
        (source / name).write_bytes(b"synthetic, never executed")
    destination = tmp_path / "registered"
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "tools/register_resources.py"),
                             str(source), "--destination", str(destination)],
                            capture_output=True, text=True, timeout=60)
    if host.IS_WINDOWS:
        assert result.returncode != 0 and "Incomplete resource tree" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    manifest = json.loads((destination / "manifest.local.json").read_text("utf8"))
    assert manifest["platform"] == "macos"
    assert set(manifest["files"]) == {"bin/fastboot", "bin/fastboot.bin", "bin/uart_boot.ino.uf2",
                                      "squashfs/sqfs2tar", "squashfs/tar2sqfs"}
    assert Assets(destination).check()["ready"] is True


def test_recovery_resources_need_only_fastboot_on_posix(tmp_path):
    from a7m4 import sd_recovery
    root = pack(tmp_path, {"bin/fastboot": b"f"})
    if host.IS_WINDOWS:
        pytest.skip("POSIX minimal recovery set")
    result = sd_recovery.check_resources(Assets(root))
    assert result["files"] == ["bin/fastboot.exe"]
    assert result["resolved"] == {"bin/fastboot.exe": "bin/fastboot"}
    assert result["camera_io"] is False and result["platform"] == "macos"


def test_payload_usb_probe_is_observation_only():
    """The macOS payload probe reads IOKit properties; it must never touch hardware."""
    source = (Path(__file__).resolve().parents[1] / "a7m4/host.py").read_text("utf8")
    start = source.index("def payload_usb_present")
    body = source[start:source.index("\ndef bootsel_volumes", start)]
    for forbidden in ("IOServiceOpen", "IOConnectCall", "write(", "subprocess", "run("):
        assert forbidden not in body, f"payload_usb_present must not {forbidden}"
    assert host.payload_usb_present() in (True, False, None)
    if not host.IS_MACOS:
        assert host.payload_usb_present() is None, "only macOS can answer this"


def test_enter_fastboot_refuses_a_boot_cycle_when_the_payload_is_already_up(tmp_path, monkeypatch):
    """A payload on the bus that fastboot cannot talk to must not trigger prepare_rp.

    On Windows the same state is caught by the PnP query and reported as
    DRIVER_BINDING. macOS has no driver to install, so the equivalent is to
    report USB_COMMUNICATION instead of injecting the payload over UART and
    rebooting a camera that is already waiting for commands.
    """
    from a7m4 import hardware
    if not host.IS_MACOS:
        pytest.skip("macOS payload probe path")

    root = pack(tmp_path, {"bin/fastboot": b"f", "bin/fastboot.bin": b"p",
                           "bin/uart_boot.ino.uf2": b"u",
                           "squashfs/sqfs2tar": b"s", "squashfs/tar2sqfs": b"t"})

    class NoDevices:
        def devices(self):
            return []

        def select(self):
            raise AssertionError("select() must not be reached")

    called = []
    monkeypatch.setattr(host, "payload_usb_present", lambda: True)
    monkeypatch.setattr(hardware, "prepare_rp", lambda *a, **k: called.append("prepare_rp"))
    with pytest.raises(ToolError) as caught:
        hardware.enter_fastboot(NoDevices(), Assets(root), lambda *a, **k: None)
    assert caught.value.code == "USB_COMMUNICATION"
    assert called == [], "must not start a boot cycle while the payload is running"


def test_enter_fastboot_boots_when_no_payload_is_on_the_bus(tmp_path, monkeypatch):
    """The negative case still takes the normal path, so the guard is not blanket."""
    from a7m4 import hardware
    if not host.IS_MACOS:
        pytest.skip("macOS payload probe path")

    root = pack(tmp_path, {"bin/fastboot": b"f", "bin/fastboot.bin": b"p",
                           "bin/uart_boot.ino.uf2": b"u",
                           "squashfs/sqfs2tar": b"s", "squashfs/tar2sqfs": b"t"})

    class AfterBoot:
        def __init__(self):
            self.calls = 0

        def devices(self):
            self.calls += 1
            return [{"serial": "syn"}] if self.calls > 1 else []

        def select(self):
            return {"product": "ILCE-7M4"}

    monkeypatch.setattr(host, "payload_usb_present", lambda: False)
    monkeypatch.setattr(hardware, "prepare_rp", lambda *a, **k: None)
    monkeypatch.setattr(hardware.time, "sleep", lambda _s: None)
    assert hardware.enter_fastboot(AfterBoot(), Assets(root), lambda *a, **k: None)["product"] == "ILCE-7M4"

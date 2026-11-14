"""Host-platform facts for the toolkit: asset names, volumes, locks, process flags.

Windows behaviour is unchanged. macOS support is additive and lives here so the
safety-critical modules (engine, transport, patching) keep one code path:

* Logical asset names stay stable. `bin/fastboot.exe` and
  `squashfs/sqfs2tar.exe` are *logical* names; on POSIX hosts the same request
  resolves to the extension-less native binary if that is what the local
  resource manifest registered. Hash pinning is unchanged either way.
* macOS needs no kernel driver for fastboot (Apple's USB stack binds the
  interface), so driver installation becomes a recorded no-op.
* RP2040 BOOTSEL volumes are `/Volumes/RPI-RP2` instead of drive letters.
* The cross-process device lock uses flock(2) instead of a Win32 named mutex.

Nothing here opens a device, writes a partition or installs software.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import sys
import threading

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_POSIX = not IS_WINDOWS

# Logical name -> ordered candidate names inside the local resource manifest.
# The native host binary is preferred on POSIX so a macOS resource pack that
# ships only `bin/fastboot` still satisfies the logical `bin/fastboot.exe` pin.
ASSET_ALIASES = {
    "bin/fastboot.exe": ("bin/fastboot", "bin/fastboot.exe") if IS_POSIX else ("bin/fastboot.exe",),
    "squashfs/sqfs2tar.exe": ("squashfs/sqfs2tar", "squashfs/sqfs2tar.exe") if IS_POSIX else ("squashfs/sqfs2tar.exe",),
    "squashfs/tar2sqfs.exe": ("squashfs/tar2sqfs", "squashfs/tar2sqfs.exe") if IS_POSIX else ("squashfs/tar2sqfs.exe",),
    "squashfs/rdsquashfs.exe": ("squashfs/rdsquashfs", "squashfs/rdsquashfs.exe") if IS_POSIX else ("squashfs/rdsquashfs.exe",),
}

# Resources every complete pack must carry on this host. Names are the reviewed
# *logical* pins; on POSIX hosts `asset_candidates` resolves the native binary.
REQUIRED_ASSETS = (
    "bin/fastboot.exe", "bin/fastboot.bin", "bin/uart_boot.ino.uf2",
    "squashfs/sqfs2tar.exe", "squashfs/tar2sqfs.exe",
) + (("driver/android_winusb.inf",) if IS_WINDOWS else ())

# Resources the SD-recovery entry point needs (no boot payload, no SquashFS).
RECOVERY_ASSETS = ("bin/fastboot.exe",) + (
    ("bin/AdbWinApi.dll", "bin/AdbWinUsbApi.dll") if IS_WINDOWS else ())

# Logical resources this toolkit *executes*. A POSIX host must be able to run
# them, so `common.Assets.get` requires the execute bit for exactly these.
# Expressed as logical names, not as "files without a suffix": an executable
# registered under a name with an extension must not slip past the check, and
# data files (`.bin`, `.uf2`, `.inf`) must not be required to be executable.
EXECUTABLE_ASSETS = ("bin/fastboot.exe", "squashfs/sqfs2tar.exe",
                     "squashfs/tar2sqfs.exe", "squashfs/rdsquashfs.exe")

PLATFORM_LABEL = "windows" if IS_WINDOWS else ("macos" if IS_MACOS else os.name)


def asset_candidates(name):
    """Manifest keys that may satisfy one logical resource name."""
    return ASSET_ALIASES.get(name, (name,))


def is_executable_asset(name):
    """Does this resource name denote something the toolkit executes?

    Keyed on the logical executable names *and their host candidates*, so a
    caller asking for the native `bin/fastboot` is held to the same execute-bit
    requirement as one asking for the logical `bin/fastboot.exe`. The gate must
    not depend on which spelling a caller happens to use.
    """
    return any(name == executable or name in asset_candidates(executable)
               for executable in EXECUTABLE_ASSETS)


def resolve_on_disk(directory, logical_name):
    """First existing path for one logical resource inside a source tree.

    Registration and verification share this so a macOS pack can ship
    `bin/fastboot` while a Windows pack ships `bin/fastboot.exe`; both are
    recorded under the logical name and hash-pinned the same way.
    """
    directory = Path(directory)
    for candidate in asset_candidates(logical_name):
        path = directory / candidate
        if path.is_file():
            return candidate, path
    return None, None


def default_data_dir():
    """Per-user state location *before* the A7M4_DATA_DIR override.

    The device lock is anchored here, not in `data_dir()`. `A7M4_DATA_DIR` is
    user-settable, so two instances started with different values would take
    different lock files while commanding the same camera — no mutual
    exclusion at all. The lock must not move with user state.
    """
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "A7M4Toolkit"
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "A7M4Toolkit"


def data_dir():
    """Per-user toolkit state; no project or camera data is written outside it.

    `A7M4_DATA_DIR` overrides the location, which is how this checkout keeps
    backups and plans on the external volume instead of the home directory.
    The device lock deliberately does *not* follow this override; see
    `lock_dir`. A relative override is resolved immediately: two processes with
    different working directories must not disagree about where state lives.
    """
    override = os.environ.get("A7M4_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return default_data_dir()


def process_flags():
    """Windows-only creation flags; POSIX hosts must not touch the constant."""
    if IS_WINDOWS:
        return getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
    return 0


def bootsel_root():
    """Where BOOTSEL mass-storage volumes appear on this host."""
    return Path("/Volumes") if IS_MACOS else None


# The fastboot payload, as it appears to a USB host. Windows detects it through
# PnP; macOS has no driver to bind but still needs to know whether the payload is
# already on the bus, because that decides between "the camera is waiting for us"
# and "the camera needs a boot cycle".
PAYLOAD_VID_PID = (0x18D1, 0x4EE0)


def payload_usb_present():
    """Is VID 18D1 / PID 4EE0 on the USB bus? Observation only, no I/O.

    Returns True, False, or None when this host cannot answer. Windows callers
    use the PnP query in `a7m4.hardware`; this is the macOS equivalent, reading
    idVendor/idProduct straight from IOKit. It forks nothing and opens no device.
    """
    if not IS_MACOS:
        return None
    import ctypes

    try:
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError:
        return None

    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    iokit.IOServiceGetMatchingServices.restype = ctypes.c_int
    iokit.IOServiceGetMatchingServices.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    iokit.IOIteratorNext.restype = ctypes.c_uint32
    iokit.IOIteratorNext.argtypes = [ctypes.c_uint32]
    iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
    iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
    iokit.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    core.CFStringCreateWithCString.restype = ctypes.c_void_p
    core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core.CFNumberGetValue.restype = ctypes.c_bool
    core.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    core.CFRelease.argtypes = [ctypes.c_void_p]

    utf8, sint32 = 0x08000100, 3
    want_vendor, want_product = PAYLOAD_VID_PID

    def integer_property(service, key):
        name = core.CFStringCreateWithCString(None, key.encode(), utf8)
        if not name:
            return None
        try:
            value = iokit.IORegistryEntryCreateCFProperty(service, name, None, 0)
            if not value:
                return None
            try:
                out = ctypes.c_int32(0)
                return out.value if core.CFNumberGetValue(value, sint32, ctypes.byref(out)) else None
            finally:
                core.CFRelease(value)
        finally:
            core.CFRelease(name)

    matching = iokit.IOServiceMatching(b"IOUSBHostDevice")
    if not matching:
        return None
    iterator = ctypes.c_uint32(0)
    if iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator)) != 0:
        return None
    found = False
    try:
        while True:
            service = iokit.IOIteratorNext(iterator)
            if not service:
                break
            try:
                if (integer_property(service, "idVendor") == want_vendor
                        and integer_property(service, "idProduct") == want_product):
                    found = True
                    break
            finally:
                iokit.IOObjectRelease(service)
    finally:
        iokit.IOObjectRelease(iterator)
    return found


def bootsel_volumes(root=None):
    """Enumerate mounted RP2040 BOOTSEL volumes without touching any device."""
    found = []
    if IS_WINDOWS:
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            volume = Path(letter + ":/")
            try:
                text = (volume / "INFO_UF2.TXT").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "Board-ID: RPI-RP2" in text and "RP2040" in text:
                found.append(volume)
        return found
    base = Path(root) if root is not None else bootsel_root()
    if base is None or not base.is_dir():
        return found
    for volume in sorted(base.iterdir()):
        try:
            text = (volume / "INFO_UF2.TXT").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Board-ID: RPI-RP2" in text and "RP2040" in text:
            found.append(volume)
    return found


LOCK_MARKER = "holder"


def prepare_lock_dir(directory):
    """Create the lock directory and keep it non-empty.

    flock(2) binds to the inode, so replacing the path drops exclusion silently.
    A non-empty directory defeats the ordinary cleanup that would do that: plain
    `rmdir` fails with ENOTEMPTY. `rm -rf` still wins, and that residue is
    covered by `verify_device_lock` at the write itself, not here.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / LOCK_MARKER).touch(exist_ok=True)
    return directory


def lock_dir():
    """Cross-process lock directory, anchored outside A7M4_DATA_DIR."""
    return prepare_lock_dir(default_data_dir() / "device-lock")


class DeviceLock:
    """Held device lock that can be re-validated before a destructive step.

    Any path-based POSIX lock can be defeated by deleting the path: flock(2)
    binds to the inode, so a replacement inode is a different lock and both
    holders proceed. `verify` compares the inode we locked against the inode the
    path names now, and every write path calls it immediately before commanding
    the camera, so a lock lost *before* that point stops the write.

    What this does NOT do, and must not be read as doing: it does not make the
    interval between the check and the command atomic. A deletion landing after
    `verify` returns — or during a long flash — is not preventable with advisory
    `flock` and repeated `stat` calls do not shrink it to zero. Only a protected
    namespace, a deletion protocol, or device-side fencing would. The marker file
    keeps ordinary `rmdir` from doing it by accident; `rm -rf` remains outside
    what this lock can promise.
    """

    def __init__(self, descriptor, target):
        self._descriptor = descriptor
        self._target = Path(target)
        self._released = False

    @property
    def target(self):
        return self._target

    def release(self):
        """Mark this lock spent. Any surviving reference now fails closed.

        List hygiene alone is not enough: a context manager entered on one
        thread and finalised on another used to leave a stale registration
        behind that still verified. Invalidating the object makes a leaked
        reference harmless rather than authoritative.
        """
        self._released = True

    def _identity(self, status):
        return (status.st_dev, status.st_ino)

    def verify(self):
        """Fail closed if the locked path no longer names the inode we hold."""
        from .common import ToolError

        if self._released:
            raise ToolError("LOCK_LOST", "This device lock was already released",
                            "A stale reference to a spent lock must not authorise a write. "
                            "Retry from an operation that holds the device lock.")
        try:
            held = os.fstat(self._descriptor)
        except OSError as error:
            # The descriptor is closed: the context that owned it has ended, so
            # this reference is spent however it survived. Report it as a lock
            # failure rather than letting a bare EBADF escape from a write path.
            raise ToolError("LOCK_LOST", "The device lock has been closed",
                            "The operation that held this lock finished; retry from a path that holds it.") from error
        try:
            on_disk = os.stat(self._target)
        except FileNotFoundError:
            raise ToolError("LOCK_LOST", "The device lock was removed while it was held",
                            "Another process or cleanup step deleted the lock path. "
                            "Close other toolkit windows and retry.") from None
        if self._identity(held) != self._identity(on_disk):
            raise ToolError("LOCK_LOST", "The device lock no longer refers to the file this process locked",
                            "The lock path was replaced while held, so mutual exclusion is not guaranteed. "
                            "Close other toolkit windows and retry.")


class KernelLock:
    """Windows device lock handle.

    A named mutex lives in the kernel namespace, not on a path, so the unlink
    race that `DeviceLock.verify` guards against cannot happen here. The object
    exists so `verify_device_lock()` still enforces its other guarantee on every
    host: a destructive step must run on a thread that is inside a held
    `hardware_lock`. Returning early on Windows instead would drop that
    invariant and, worse, would have made every write fail once the check was
    wired into the write paths.

    Inherited from upstream and deliberately unchanged: `WAIT_ABANDONED` is
    accepted as acquisition. That transfers exclusive ownership when the previous
    owner died, but it is not evidence that the previous owner's already-issued
    camera command finished — an outstanding fastboot child can outlive its
    parent. Neither better nor worse than upstream; recorded, not fixed.
    """

    def __init__(self, handle):
        self._handle = handle
        self._released = False

    def release(self):
        """Mark this lock spent so a leaked reference cannot authorise a write."""
        self._released = True

    def verify(self):
        from .common import ToolError

        if self._released:
            raise ToolError("LOCK_LOST", "This device lock was already released",
                            "A stale reference to a spent lock must not authorise a write. "
                            "Retry from an operation that holds the device lock.")
        return None


# Held device locks, scoped PER THREAD, innermost last.
#
# Thread-local is the correct authority scope. An earlier draft of this file used
# a process-global stack and argued that thread-local was weaker; that reasoning
# was backwards, and two independent reviews plus a local reproduction proved it.
# Verification answers the question "did *this* execution acquire a lock, and does
# it still hold it?" — a process-global list let one thread validate another
# thread's authority. Reproduced fail-open: a thread that never called
# `hardware_lock()` passed `verify_device_lock()` while an unrelated thread held
# the lock. Worse, with the default path: if the lock directory is deleted and
# recreated, a second holder locks the new inode and appends it, and the stale
# holder's verification then inspects the *replacement* holder's valid inode and
# passes — missing its own `LOCK_LOST`, so two ordinary writers proceed.
#
# Under a per-thread stack, a thread that skipped `hardware_lock` sees an empty
# stack and raises `LOCK`. That is fail-closed, which is the property the guard
# exists to provide.
#
# A stack rather than one slot because a Win32 named mutex is recursively
# acquirable by its owning thread: same-thread nesting must unwind to the right
# entry, and a single slot would be cleared by the inner release and then wrongly
# refuse the outer holder's next write.
#
# No legitimate cross-thread handoff exists in this toolkit: `Engine` serialises
# its operations with an instance mutex, `recover_sd.main` acquires and calls
# `recover()` on the same thread, and nothing under `a7m4/` spawns a worker that
# operates the device. A lock acquired on one thread is only ever used from it.
_LOCK_STATE = threading.local()


def active_locks():
    """The device locks held by the calling thread, innermost last."""
    stack = getattr(_LOCK_STATE, "stack", None)
    if stack is None:
        stack = _LOCK_STATE.stack = []
    return stack


def verify_device_lock():
    """Called by write paths right before commanding the camera.

    Raises `LOCK` when this thread holds no device lock, meaning the caller
    reached a destructive step without going through `hardware_lock`, and
    `LOCK_LOST` when the lock it does hold is no longer valid.
    """
    from .common import ToolError

    stack = active_locks()
    try:
        held = stack[-1]
    except IndexError:
        raise ToolError("LOCK", "Refusing a device operation without the device lock")
    held.verify()


@contextmanager
def hardware_lock(path=None):
    """Serialize every toolkit process that may command the camera.

    Windows uses a local named mutex. POSIX hosts take an exclusive flock(2) on
    a per-user lock directory and refuse rather than block, so a second window
    cannot interleave a write with another process's readback. The lock is
    anchored in `default_data_dir()`, *not* in `data_dir()`: `A7M4_DATA_DIR` is
    user-settable, and two instances started with different overrides would
    otherwise take different locks while commanding the same camera.

    The lock target must be a directory. An explicit `path` is honoured for
    tests; a file target is rejected because deleting it is the ordinary way to
    drop exclusion.

    See `DeviceLock.verify` for what happens if the directory is removed while
    held. Holders call `verify_device_lock()` before every write.
    """
    from .common import ToolError, require

    if IS_WINDOWS:
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = None
        acquired = False
        try:
            # One protected region from the acquisition onward: the kernel object
            # is released and closed whatever fails inside, including a failure
            # while constructing or registering the wrapper. An earlier
            # restructure built the wrapper outside the cleanup block and leaked
            # the owned handle if that construction raised.
            handle = kernel.CreateMutexW(None, False, "Local\\A7M4FastbootToolkit-Hardware")
            require(handle, "LOCK", "Could not create device lock")
            # WAIT_ABANDONED (0x80) is accepted, as upstream does: the previous
            # owner died. It transfers exclusive ownership; it is *not* proof
            # that the previous owner's outstanding fastboot child finished, so a
            # camera operation can still be in flight.
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise ToolError("BUSY", "Another toolkit process is using the camera")
            held = KernelLock(handle)
            # Capture the owning thread's list here: resolving `active_locks()`
            # in the finally would use whichever thread finalises the context, so
            # a context entered on one thread and closed on another left the
            # owner's registration behind as a stale, still-verifying entry.
            stack = active_locks()
            try:
                # Registration is the first statement inside the protected block,
                # so an interruption after the push but before the body still runs
                # the removal.
                stack.append(held)
                yield held
            finally:
                # Release first: an interruption inside cleanup must leave a
                # harmless survivor, never an authoritative one.
                held.release()
                if stack and stack[-1] is held:
                    stack.pop()
                elif held in stack:
                    stack.remove(held)
        finally:
            if handle:
                if acquired:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
        return

    import fcntl
    import stat
    target = Path(path) if path is not None else lock_dir()
    if target.exists() and not stat.S_ISDIR(os.stat(target).st_mode):
        raise ToolError("LOCK", "Device lock target must be a directory",
                        "Pass a directory; a lock file can be unlinked and recreated, "
                        "which silently drops exclusion.")
    prepare_lock_dir(target)
    require(stat.S_ISDIR(os.stat(target).st_mode), "LOCK",
            "Device lock target must be a directory",
            "Pass a directory; a lock file can be unlinked and recreated, which silently drops exclusion.")
    descriptor = None
    try:
        # Opened inside the protected region: an interruption between the open
        # returning and the cleanup block being installed would otherwise leak a
        # bare file descriptor, which has no finaliser to close it.
        descriptor = os.open(target, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ToolError("BUSY", "Another toolkit process is using the camera") from e
        held = DeviceLock(descriptor, target)
        # Capture the owning thread's list here, not inside the finally: a
        # context entered on one thread and finalised on another would otherwise
        # remove from the closing thread's list and leave the owner's entry
        # behind as a stale, still-verifying registration.
        stack = active_locks()
        try:
            # Pushed inside the protected block so an interruption between the
            # push and the body still unwinds it.
            stack.append(held)
            yield held
        finally:
            # Invalidate the object BEFORE touching the list. An asynchronous
            # interruption can land anywhere in a cleanup block, and if it lands
            # between the removal statements the entry survives; releasing first
            # means any survivor refuses to verify instead of authorising a write.
            held.release()
            if stack and stack[-1] is held:
                stack.pop()
            elif held in stack:
                stack.remove(held)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        if descriptor is not None:
            os.close(descriptor)

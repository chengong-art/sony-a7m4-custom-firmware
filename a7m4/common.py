from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import host


class ToolError(Exception):
    def __init__(self, code, message, hint=""):
        super().__init__(message)
        self.code, self.hint = code, hint


def require(condition, code, message, hint=""):
    if not condition:
        raise ToolError(code, message, hint)


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def root_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir():
    return host.data_dir()


def run(args, *, cwd=None, timeout=300, stdin=None, stdout=None):
    # No shell; filenames are arguments, never executable command text.
    flags = host.process_flags()
    try:
        result = subprocess.run([str(a) for a in args], cwd=cwd, stdin=stdin,
                                stdout=stdout or subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout,
                                creationflags=flags)
    except subprocess.TimeoutExpired as e:
        raise ToolError("TIMEOUT", "Operation timed out", "Check cables and power; reconnect and create a new backup before retrying.") from e
    except OSError as e:
        raise ToolError("EXEC_FAILED", str(e), "Check the offline resource pack.") from e
    out = (result.stdout or b"") + (result.stderr or b"")
    require(result.returncode == 0, "COMMAND_FAILED", out.decode("utf-8", "replace")[-6000:])
    return out


class Assets:
    """Hash-pinned local resource pack; never a distributed binary bundle.

    The pack location defaults to `<repo>/vendor`. `A7M4_RESOURCES` may point
    at another registered pack so a macOS user can keep binaries outside the
    checkout without weakening the manifest hash checks.
    """

    def __init__(self, base=None):
        override = os.environ.get("A7M4_RESOURCES")
        self.base = Path(base or override or root_dir() / "vendor")

    def manifest(self):
        manifest_path = self.base / "manifest.local.json"
        require(manifest_path.is_file(), "ASSETS_MISSING", "Offline resource manifest is missing", "Run tools/import_local_resources.py or install a complete local build.")
        return read_json(manifest_path)

    def entry(self, relative, manifest=None):
        """Locate one logical resource, honouring host-specific native names.

        Manifest keys are the reviewed *logical* names (`bin/fastboot.exe`,
        `squashfs/sqfs2tar.exe`). A pack may register the native binary under a
        host-specific key, or keep the logical key and record the real file in
        its `path` field. Either way the recorded SHA-256 must match, so a
        swapped binary is still rejected.
        """
        manifest = manifest or self.manifest()
        files = manifest.get("files", {})
        for candidate in (relative, *host.asset_candidates(relative)):
            if candidate in files:
                record = files[candidate]
                return record.get("path", candidate), record
        return None, None

    def get(self, relative):
        manifest = self.manifest()
        name, expected = self.entry(relative, manifest)
        require(name is not None, "ASSETS_MISSING", f"Resource is not registered: {relative}",
                "Register the host-appropriate binary name; see docs/setup-macos.md.")
        root = (self.base / "local").resolve()
        path = (root / name).resolve()
        require(path.is_relative_to(root), "PATH", "Invalid resource path")
        require(path.is_file(), "ASSETS_MISSING", f"Missing resource: {relative}")
        require(sha256(path) == expected["sha256"], "ASSET_INTEGRITY", f"Modified resource: {relative}")
        if host.IS_POSIX and (host.is_executable_asset(relative) or host.is_executable_asset(name)):
            # A permission problem is not a tampering problem; report it as its
            # own code so the advice matches the cause. `is_file` above already
            # rejected directories, and the hash above already covered content.
            # Keyed on the request *and* the resolved registration so a caller
            # asking for the native `bin/fastboot` is gated exactly like one
            # asking for the logical `bin/fastboot.exe`.
            require(os.access(path, os.X_OK), "ASSET_PERMISSION", f"Resource is not executable: {relative}",
                    "chmod +x the registered host binary; hash pinning is unaffected.")
        return path

    def check(self):
        manifest = read_json(self.base / "manifest.local.json") if (self.base / "manifest.local.json").exists() else {"files": {}}
        errors = []
        required = list(host.REQUIRED_ASSETS)
        if host.IS_WINDOWS and "driver/zadig.exe" in manifest["files"]:
            required = [name for name in required if name != "driver/android_winusb.inf"]
            required += ["driver/zadig.exe", "driver/zadig.ini", "driver/A7M4-Fastboot.cfg"]
        for name in set(required) | set(manifest["files"]):
            try:
                self.get(name)
            except ToolError as e:
                errors.append(str(e))
        return {"ready": not errors, "errors": errors, "distribution": manifest.get("distribution", "missing"),
                "platform": host.PLATFORM_LABEL}


def event(callback, phase, **values):
    if callback:
        callback({"type": "progress", "phase": phase, "time": time.time(), **values})

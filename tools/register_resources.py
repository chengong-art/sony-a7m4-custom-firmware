"""Register a user-supplied local resource tree; never download or operate devices.

The required *logical* names are the reviewed pins (`bin/fastboot.exe`,
`squashfs/sqfs2tar.exe`). On macOS the same logical resource is the native
extension-less binary, so this tool resolves either name on disk and records
what it actually copied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a7m4 import host

# Extension-less host tools that a POSIX resource pack may register instead of
# their Windows `.exe` counterparts.
NATIVE_TOOLS = {"fastboot", "sqfs2tar", "tar2sqfs", "rdsquashfs", "gensquashfs"}

# Sibling libraries a native tool needs at load time. On Windows that is
# `AdbWinApi.dll` next to `fastboot.exe`; on macOS the squashfs-tools-ng build
# links `@loader_path/libsquashfs.1.dylib`, so the library must travel with the
# tools or every squashfs command fails with "Library not loaded". A pack that
# omits it registers "successfully" and only breaks when the tool is first run.
NATIVE_LIBRARIES = (".dylib", ".so")


def selectable(path):
    suffix = path.suffix.lower()
    if suffix in {".exe", ".dll", ".bin", ".uf2", ".txt", ".md", ".inf", ".cat", ".properties"}:
        return True
    if suffix in NATIVE_LIBRARIES:
        return True
    return suffix == "" and path.name in NATIVE_TOOLS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory with bin/, driver/, squashfs/")
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "vendor")
    parser.add_argument('--recovery-only', action='store_true', help='Register only the fastboot host binary and adjacent DLLs/notices; camera must already be in fastboot')
    args = parser.parse_args()
    source, destination = args.directory.resolve(), args.destination.resolve()
    required = (("bin/fastboot.exe",) if args.recovery_only else
                ("bin/fastboot.exe", "bin/fastboot.bin", "bin/uart_boot.ino.uf2",
                 "squashfs/sqfs2tar.exe", "squashfs/tar2sqfs.exe") if host.IS_POSIX else
                ("bin/fastboot.exe", "bin/fastboot.bin", "bin/uart_boot.ino.uf2",
                 "driver/android_winusb.inf", "squashfs/sqfs2tar.exe", "squashfs/tar2sqfs.exe"))
    missing = [name for name in required if host.resolve_on_disk(source, name)[1] is None]
    if missing:
        raise SystemExit("Incomplete resource tree (missing " + ", ".join(missing) + "). See docs/setup.md or docs/setup-macos.md")
    if (destination / "manifest.local.json").exists():
        raise SystemExit("Manifest exists; use a new destination or deliberately manage your local pack")
    selected = []
    for folder in ("bin", "driver", "squashfs"):
        for path in (source / folder).rglob("*"):
            if path.is_file() and selectable(path):
                relative = path.relative_to(source)
                if not path.resolve().is_relative_to(source):
                    raise SystemExit("Linked resource escapes input directory")
                selected.append((path, relative))
    if args.recovery_only:
        # Keep the Windows fastboot siblings, notices and the native binary.
        selected = [(path, rel) for path, rel in selected
                    if rel.parts[0] == 'bin'
                    and (rel.name in NATIVE_TOOLS
                         or (path.suffix.lower() in ('.exe', '.dll', '.txt', '.md')
                             and (path.suffix.lower() != '.exe' or path.name == 'fastboot.exe')))]
    files = {}
    for path, relative in selected:
        target = destination / "local" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != target.resolve():
            # copy2 keeps the mode: a native library copied with copyfile loses
            # its execute bits and the loader can then refuse it on some hosts.
            shutil.copy2(path, target)
        if host.IS_POSIX and (relative.suffix.lower() in NATIVE_LIBRARIES or
                              any(relative.as_posix() in host.asset_candidates(name)
                                  for name in host.EXECUTABLE_ASSETS)):
            # Hash pinning is independent of the mode bit, but the host must be
            # able to execute what it registered. Registering `bin/fastboot`
            # under the Windows logical name counts: the resource, not the
            # suffix on disk, decides whether the execute bit is required.
            target.chmod(target.stat().st_mode | 0o111)
        files[relative.as_posix()] = {"size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                                      "source": "user-supplied; record exact upstream revisions separately"}
    manifest = {"schema": 1, "platform": host.PLATFORM_LABEL,
                "distribution": "LOCAL-ONLY; external component redistribution not cleared", "files": files}
    (destination / "manifest.local.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"resources_registered": len(files), "camera_io": False,
                      "destination": str(destination), "platform": host.PLATFORM_LABEL}))


if __name__ == "__main__":
    main()

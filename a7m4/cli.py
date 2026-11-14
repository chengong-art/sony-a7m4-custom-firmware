"""JSON-RPC 2.0 over newline-delimited UTF-8 stdio; no TCP listener."""
import argparse
import json
from pathlib import Path
import sys
from .common import Assets, ToolError, read_json
from .engine import Engine, validate_backup
from . import state

STATE_SCHEMA = {
    "api_version": "int, this contract's version",
    "protocol": "'jsonrpc-2.0-stdio'",
    "toolkit_version": "package version",
    "sections": "which sections this response contains",
    "state": "session state: disconnected | connecting | backing_up | ready | busy",
    "write_enabled": "bool; true only while a fresh complete backup is held in this session",
    "backup": "path of the session backup folder, or null",
    "partitions": "known partitions with writable flags (empty until connected)",
    "last_result": "result of the last completed operation, or null",
    "session": {"connected": "bool; a camera session exists in this process",
                "fastboot": "bool; partitions were enumerated, i.e. fastboot answered",
                "backup_folder": "string or null",
                "backup_complete": "bool or null; read from that folder's manifest",
                "device": "identity read from the manifest, or null",
                "plans_pending": "int; reviewed plans awaiting apply",
                "partitions_known": "int"},
    "next_actions": "methods that make sense in this state (guidance, not policy)",
    "resources": {"ready": "bool; the hash-pinned pack checks out",
                  "errors": "list of strings",
                  "distribution": "licence note for the external binaries"},
    "device": {"payload_usb_present": "bool; observation only, no device command",
               "bootsel_volumes": "mounted RP2040 BOOTSEL volumes",
               "observation_only": "always true; this section never commands the camera"},
    "host": {"platform": "'macos' | 'linux' | 'windows'",
             "data_dir": "where state, backups and keys live",
             "lock": {"path": "device-lock directory",
                      "held_by_this_process": "bool",
                      "note": "a different process holding it surfaces as BUSY on write"}},
    "backups": {"root": "backups directory",
                "count": "int; folders on disk",
                "returned": "int; how many are described below",
                "items": [{"folder": "path", "name": "folder name",
                           "complete": "bool or null", "created": "unix time or null",
                           "partitions": "int or null", "device": "identity or null",
                           "readable": "bool; manifest parsed"}]},
}

METHODS = {
    "status": {"description": "Unified read-only state snapshot; no device commands",
               "safety": "read", "params": {"include": "optional list of sections: " + ", ".join(state.SECTIONS),
                          "backup_limit": "optional int 0-200, default 10"}},
    "doctor": {"description": "Validate bundled resources; no driver installation", "safety": "read", "params": {}},
    "connect_backup": {"description": "Enter fastboot, install driver/UF2 if needed, then mandatory full backup", "safety": "session", "params": {"destination": "optional directory", "enter": "optional bool, default true; false requires existing fastboot"}},
    "inspect": {"description": "Read version and updater state from verified session snapshots", "safety": "read", "params": {}},
    "keygen": {"description": "Create local RSA-2048 key pair; never returns key content", "safety": "key", "params": {"directory": "optional directory"}},
    "prepare": {"description": "Build a reviewable image from verified camera backups; no camera write", "safety": "read", "params": {"action": "unlock | set_version | restore | restore_official", "value": "version for set_version", "public_key_path": "public PEM for unlock", "source_backup": "complete backup folder for restore/restore_official", "partition": "partition for restore"}},
    "apply": {"description": "Apply one reviewed in-session plan and verify full readback", "safety": "write", "params": {"plan_id": "returned by prepare", "confirmation": "exact confirmation string returned by prepare"}},
    "reboot": {"description": "Leave fastboot; invalidates session write permission", "safety": "session", "params": {}}
}


def dispatch(engine, method, params):
    if method == "capabilities":
        if not isinstance(params, dict) or params:
            raise ToolError("PARAMS", "capabilities accepts no parameters")
        return {"protocol": "jsonrpc-2.0-stdio", "api_version": 1, "methods": METHODS,
                "state_schema": STATE_SCHEMA, "state_sections": list(state.SECTIONS),
                "backup_required": True, "arbitrary_commands": False,
                "notifications": {"progress": "emitted while a method runs; same envelope as a request, no id"},
                "safety_levels": {
                    "read": "no device command; safe to poll",
                    "session": "talks to the camera but writes nothing",
                    "write": "writes to the camera; needs a complete backup in this session",
                    "key": "writes a key pair under data_dir only"}}
    if method not in METHODS:
        raise ToolError("METHOD", "Unknown method")
    if not isinstance(params, dict) or set(params) - set(METHODS[method]["params"]):
        raise ToolError("PARAMS", "Unknown or invalid parameters")
    if method == "doctor":
        return engine.assets.check()
    if method == "status":
        return state.snapshot(engine, include=params.get("include"),
                              backup_limit=params.get("backup_limit", 10))
    if method == "connect_backup" and "enter" in params:
        if type(params["enter"]) is not bool:
            raise ToolError("PARAMS", "enter must be a boolean")
    return getattr(engine, method)(**params)


def serve(engine, stream_in=None, stream_out=None):
    source, sink = stream_in or sys.stdin, stream_out or sys.stdout
    def send(value):
        sink.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"))+"\n")
        sink.flush()
    engine.callback = lambda value: send({"jsonrpc": "2.0", "method": "progress", "params": value})
    for raw in source:
        request = None
        try:
            if len(raw) > 1024*1024:
                raise ToolError("REQUEST_SIZE", "Request exceeds 1 MiB")
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str) or "id" not in request or isinstance(request["id"], (dict, list, bool)):
                raise ToolError("REQUEST", "Expected a JSON-RPC request with scalar id")
            result = dispatch(engine, request["method"], request.get("params", {}))
            send({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except Exception as e:
            code = e.code if isinstance(e, ToolError) else "INVALID_REQUEST" if isinstance(e, (ValueError, TypeError, KeyError)) else "INTERNAL"
            send({"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                  "error": {"code": -32000, "message": str(e), "data": {"code": code, "hint": getattr(e, "hint", "")}}})


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="A7M4 Fastboot Toolkit")
    parser.add_argument("--demo", action="store_true", help="Use synthetic camera only")
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("agent", help="JSON-RPC 2.0 over stdio; the interface agents should use")
    sub.add_parser("doctor")
    snapshot_parser = sub.add_parser("state", help="Print the unified state snapshot as JSON and exit")
    snapshot_parser.add_argument("--include", help="comma-separated sections: " + ", ".join(state.SECTIONS))
    snapshot_parser.add_argument("--backup-limit", type=int, default=10)
    sub.add_parser("enter", help="Enter Fastboot without reading or writing partitions")
    sub.add_parser("install-driver", help="Open the Fastboot driver installer with administrator confirmation")
    sub.add_parser("exit", aliases=["reboot"], help="Leave Fastboot and reboot the camera")
    backup = sub.add_parser("backup")
    backup.add_argument("--destination", type=Path)
    backup.add_argument("--already-fastboot", action="store_true")
    verify = sub.add_parser("verify-backup")
    verify.add_argument("folder", type=Path)
    from .maintenance import ACTIONS
    console = sub.add_parser("maintain", help="Advanced interactive workflow; always back up first")
    console.add_argument("--action", choices=ACTIONS, default="menu")
    console.add_argument("--destination", type=Path)
    console.add_argument("--already-fastboot", action="store_true", help="Skip boot preparation only; full backup is still required")
    args = parser.parse_args()
    from .demo import DemoTransport
    engine = Engine(transport=DemoTransport() if args.demo else None, home=args.data_dir)
    if args.command == "agent":
        if args.demo:
            original = engine.connect_backup
            engine.connect_backup = lambda destination=None, enter=True: original(destination, enter=False)
        serve(engine)
        return
    try:
        if args.command == "maintain":
            from .maintenance import run_maintenance
            run_maintenance(engine, action=args.action, destination=args.destination,
                        enter=not args.already_fastboot and not args.demo)
            return
        if args.command == "state":
            include = [part.strip() for part in args.include.split(",") if part.strip()] if args.include else None
            print(json.dumps(state.snapshot(engine, include=include, backup_limit=args.backup_limit),
                             ensure_ascii=False, indent=2))
            return
        if args.command == "doctor":
            result = engine.assets.check()
        elif args.command == "enter":
            if args.demo:
                result = engine.transport.select()
            else:
                from .hardware import enter_fastboot
                engine.callback = lambda data: print(json.dumps(data, ensure_ascii=False), flush=True)
                result = enter_fastboot(engine.transport, engine.assets, engine.callback)
        elif args.command in ("exit", "reboot"):
            result = engine.reboot()
        elif args.command == "install-driver":
            if args.demo:
                raise ToolError("DEMO", "Driver installation is unavailable in demo mode")
            from .hardware import install_driver
            engine.callback = lambda data: print(json.dumps(data, ensure_ascii=False), flush=True)
            driver = install_driver(engine.assets, engine.callback)
            if driver.get("driver") == "not-required-on-macos":
                result = {"driver": driver["driver"], "camera_io": driver["camera_io"],
                          "message": "macOS 无需安装驱动；连接相机后运行只检查fastboot连接。 / No driver is needed on macOS; run the read-only connection check after connecting the camera."}
            else:
                result = {"driver": driver.get("driver"), "camera_io": driver.get("camera_io"),
                          "message": "驱动窗口已关闭；连接相机后运行只检查fastboot连接。 / Run the read-only connection check after connecting the camera."}
        elif args.command == "verify-backup":
            result = validate_backup(args.folder)
        else:
            engine.callback = lambda data: print(json.dumps(data, ensure_ascii=False), flush=True)
            result = engine.connect_backup(args.destination, enter=not args.already_fastboot and not args.demo)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "doctor" and not result["ready"]:
            raise SystemExit(1)
    except Exception as e:
        print(json.dumps({"error": str(e), "code": getattr(e, "code", "INTERNAL"), "hint": getattr(e, "hint", "")}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

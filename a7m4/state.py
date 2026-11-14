"""Unified, read-only state snapshot for agents.

`snapshot()` is the single place that answers "what is the state of this toolkit
right now". It is deliberately read-only: it reads the in-memory session, the
filesystem and (on macOS) the USB tree, and it never issues a device command. An
agent can poll it as often as it likes without moving the camera.

The JSON shape is a contract; see `docs/agent-api.md`. Existing keys of
`Engine.status()` are kept at the top level so older clients keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__, host
from .common import ToolError

SECTIONS = ("session", "resources", "device", "host", "backups")

# What an agent should read next for each session state. Guidance, not policy:
# the guards that actually gate a write live in Engine and are unchanged.
NEXT_ACTIONS = {
    "disconnected": ["doctor", "connect_backup"],
    "connecting": [],
    "backing_up": [],
    "ready": ["inspect", "prepare", "apply", "reboot"],
    "busy": [],
}


def _resources(engine):
    """Asset check. Hashes the pack, so it is the slowest section (~0.3 s)."""
    try:
        result = engine.assets.check()
    except Exception as error:                      # a broken pack must not break the snapshot
        return {"ready": False, "errors": [f"{type(error).__name__}: {error}"]}
    return {"ready": bool(result.get("ready")),
            "errors": list(result.get("errors") or []),
            "distribution": result.get("distribution"),
            "checked": True}


def _device():
    """Observation only: is the payload USB device here, is a BOOTSEL volume mounted."""
    present = False
    volumes: list[str] = []
    try:
        present = bool(host.payload_usb_present())
    except Exception:
        pass
    try:
        volumes = [str(volume) for volume in host.bootsel_volumes()]
    except Exception:
        pass
    return {"payload_usb_present": present, "bootsel_volumes": volumes,
            "observation_only": True}


def _host(engine):
    held = False
    try:
        held = bool(host.active_locks())
    except Exception:
        pass
    try:
        lock_path = str(host.lock_dir())
    except Exception:
        lock_path = None
    return {"platform": host.PLATFORM_LABEL, "data_dir": str(engine.home),
            "lock": {"path": lock_path, "held_by_this_process": held,
                     "note": ("advisory: whether *this* process holds the device lock. "
                              "A different process holding it is only discoverable by "
                              "attempting the write, which fails with BUSY.")}}


def _backups(engine, limit):
    root = Path(engine.home) / "backups"
    items = []
    if root.is_dir():
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
            entry = {"folder": str(folder), "name": folder.name, "complete": None,
                     "created": None, "partitions": None, "device": None, "readable": False}
            manifest = folder / "manifest.json"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text("utf8"))
                except Exception as error:
                    entry["error"] = f"manifest unreadable: {error}"
                else:
                    entry.update({"readable": True, "complete": bool(data.get("complete")),
                                  "created": data.get("created"),
                                  "partitions": len(data.get("partitions") or []),
                                  "device": data.get("device"),
                                  "session": data.get("session")})
            items.append(entry)
    return {"root": str(root), "count": len(items), "returned": min(len(items), limit),
            "items": items[:limit]}


def _session(engine):
    """The in-memory session, plus an explicit view of what may be done next."""
    base = engine.status()
    manifest = None
    if engine.backup:
        try:
            manifest = json.loads((Path(engine.backup) / "manifest.json").read_text("utf8"))
        except Exception:
            manifest = None
    detail = {
        "connected": bool(engine.parts),
        "fastboot": bool(engine.parts),
        "backup_folder": str(engine.backup) if engine.backup else None,
        "backup_complete": bool(manifest.get("complete")) if manifest else None,
        "device": (manifest or {}).get("device"),
        "plans_pending": len(getattr(engine, "plans", {}) or {}),
        "partitions_known": len(getattr(engine, "parts", {}) or {}),
    }
    return base, detail


def snapshot(engine, include=None, backup_limit=10):
    """Return the unified state snapshot.

    include: optional subset of SECTIONS. Default is every section.
    backup_limit: how many recent backup folders to describe in detail.
    """
    if include is not None:
        if not isinstance(include, list) or not all(isinstance(name, str) for name in include):
            raise ToolError("PARAMS", "include must be a list of section names")
        unknown = sorted(set(include) - set(SECTIONS))
        if unknown:
            raise ToolError("PARAMS", f"Unknown sections: {', '.join(unknown)}",
                            f"Known sections: {', '.join(SECTIONS)}")
    if not isinstance(backup_limit, int) or isinstance(backup_limit, bool) or backup_limit < 0 or backup_limit > 200:
        raise ToolError("PARAMS", "backup_limit must be an integer between 0 and 200")

    wanted = set(include) if include is not None else set(SECTIONS)
    result: dict = {
        "api_version": 1,
        "protocol": "jsonrpc-2.0-stdio",
        "toolkit_version": __version__,
        "sections": sorted(wanted),
    }
    if "session" in wanted:
        base, detail = _session(engine)
        result.update(base)                       # unchanged Engine.status() keys
        result["session"] = detail
        result["next_actions"] = NEXT_ACTIONS.get(base.get("state"), [])
    if "resources" in wanted:
        result["resources"] = _resources(engine)
    if "device" in wanted:
        result["device"] = _device()
    if "host" in wanted:
        result["host"] = _host(engine)
    if "backups" in wanted:
        result["backups"] = _backups(engine, backup_limit)
    return result

"""The agent interface is a contract, so it gets contract tests.

Two things matter here and neither is about the camera: that the envelope and the
error codes stay stable, and that `status` really is a read-only observation. The
second is asserted the only way that proves it — by taking a snapshot with a
transport that raises on any device call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a7m4 import state as state_module          # noqa: E402
from a7m4.engine import Engine                  # noqa: E402


class ExplodingTransport:
    """Any device call at all fails the test that uses this."""

    def __getattr__(self, name):
        raise AssertionError(f"the snapshot attempted a device call: {name}")


def run_agent(requests, data_dir):
    env = dict(os.environ, A7M4_DATA_DIR=str(data_dir))
    proc = subprocess.Popen([sys.executable, "-B", "-m", "a7m4", "--demo", "agent"],
                            cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
    replies = []
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            replies.append(json.loads(proc.stdout.readline()))
    finally:
        proc.stdin.close()
        proc.wait(timeout=20)
    assert proc.returncode == 0, proc.stderr.read()
    return replies


def test_capabilities_advertises_the_state_contract(tmp_path):
    (reply,) = run_agent([{"jsonrpc": "2.0", "id": 1, "method": "capabilities"}], tmp_path / "state")
    result = reply["result"]
    assert result["protocol"] == "jsonrpc-2.0-stdio"
    assert result["api_version"] == 1
    assert result["arbitrary_commands"] is False
    assert set(result["state_sections"]) == {"session", "resources", "device", "host", "backups"}
    assert "status" in result["methods"] and "apply" in result["methods"]
    for field in ("state", "session", "backups", "host", "resources", "device", "next_actions"):
        assert field in result["state_schema"], f"state_schema must document {field}"
    assert "progress" in result["notifications"]


def test_error_codes_are_stable(tmp_path):
    replies = run_agent([
        {"jsonrpc": "2.0", "id": 1, "method": "status", "params": {"include": ["nonsense"]}},
        {"jsonrpc": "2.0", "id": 2, "method": "no_such_method", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "status", "params": {"unknown_key": 1}},
        {"jsonrpc": "2.0", "id": 4, "method": "status", "params": {"backup_limit": "many"}},
        {"jsonrpc": "2.0", "id": 5, "method": "doctor", "params": {}},
        {"jsonrpc": "2.0", "id": 6, "method": "status", "params": {}},
    ], tmp_path / "state")
    assert [r["error"]["data"]["code"] for r in replies[:4]] == ["PARAMS", "METHOD", "PARAMS", "PARAMS"]
    assert "result" in replies[4], replies[4]
    assert "result" in replies[5], replies[5]
    assert replies[0]["id"] == 1 and replies[5]["id"] == 6, "ids must round-trip"


def test_include_selects_sections_and_nothing_else(tmp_path):
    (reply,) = run_agent([{"jsonrpc": "2.0", "id": 1, "method": "status",
                           "params": {"include": ["session", "host"]}}], tmp_path / "state")
    result = reply["result"]
    assert result["sections"] == ["host", "session"]
    assert "device" not in result and "backups" not in result and "resources" not in result
    assert result["host"]["platform"] == "macos"
    assert result["host"]["lock"]["held_by_this_process"] is False


def test_snapshot_is_read_only_even_with_a_broken_transport(tmp_path):
    """The strongest read-only claim: a transport that raises on any call is fine."""
    engine = Engine(transport=ExplodingTransport(), home=tmp_path / "state")
    snapshot = state_module.snapshot(engine)
    assert snapshot["state"] == "disconnected"
    assert snapshot["write_enabled"] is False
    assert snapshot["device"]["observation_only"] is True
    assert snapshot["host"]["lock"]["held_by_this_process"] is False


def test_backup_inventory_reflects_what_is_on_disk(tmp_path):
    home = tmp_path / "state"
    folder = home / "backups" / "20260101-000001-good"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "schema": 1, "complete": True, "session": "abc", "created": 1792874931.39,
        "device": {"product": "ILCE-7M4", "transport_serial": "SYNTHETIC", "serial_is_unique": False},
        "partitions": [{"name": "nflasha1"}, {"name": "nflasha2"}],
    }), "utf8")
    broken = home / "backups" / "20260101-000000-broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", "utf8")

    engine = Engine(home=home)
    snapshot = state_module.snapshot(engine, include=["backups"])
    backups = snapshot["backups"]
    assert backups["count"] == 2 and backups["returned"] == 2
    # Sorted by name descending, so the newest timestamp comes first.
    newest = backups["items"][0]
    assert newest["complete"] is True and newest["partitions"] == 2 and newest["readable"] is True
    older = backups["items"][1]
    assert older["readable"] is False and "error" in older, "a broken manifest must be reported, not crash"

    limited = state_module.snapshot(engine, include=["backups"], backup_limit=1)["backups"]
    assert limited["count"] == 2 and limited["returned"] == 1 and len(limited["items"]) == 1


def test_one_shot_state_agrees_with_the_protocol(tmp_path):
    env = dict(os.environ, A7M4_DATA_DIR=str(tmp_path / "state"))
    one_shot = subprocess.run([sys.executable, "-B", "-m", "a7m4", "state"], cwd=str(ROOT),
                              capture_output=True, text=True, env=env, timeout=60)
    assert one_shot.returncode == 0, one_shot.stderr
    from_cli = json.loads(one_shot.stdout)
    (reply,) = run_agent([{"jsonrpc": "2.0", "id": 1, "method": "status", "params": {}}], tmp_path / "state")
    from_rpc = reply["result"]
    assert set(from_cli) == set(from_rpc), "the one-shot command must not drift from the method"
    assert from_cli["sections"] == from_rpc["sections"]


def test_one_shot_include_argument_is_honoured(tmp_path):
    env = dict(os.environ, A7M4_DATA_DIR=str(tmp_path / "state"))
    result = subprocess.run([sys.executable, "-B", "-m", "a7m4", "state", "--include", "session"],
                            cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["sections"] == ["session"]
    assert "backups" not in payload

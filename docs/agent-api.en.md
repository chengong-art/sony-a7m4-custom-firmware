# Agent interface

[中文](agent-api.md) · [Back](../README.md)

The toolkit exposes exactly one interface to agents: **JSON-RPC 2.0 over stdio**.
There is no TCP listener and nothing to click — AI clients and scripts use the same
path, and so does the graphical window.

```bash
a7m4 agent              # long-lived session: one request per line, one reply per line
a7m4 --demo agent       # same, on a synthetic camera
a7m4 state              # one-shot: print the state snapshot and exit
a7m4 state --include session,host --backup-limit 3
```

## Envelope

One JSON object per line in each direction. Reponses carry the request `id`;
notifications have no `id` and arrive while a method runs.

```json
{"jsonrpc":"2.0","id":1,"method":"status","params":{"include":["session","host"]}}
{"jsonrpc":"2.0","id":1,"result":{ ... }}
{"jsonrpc":"2.0","method":"progress","params":{"phase":"backup","partition":"nflasha1","percent":42}}
```

stdout carries protocol traffic only — never logs. Requests are capped at 1 MiB
(`REQUEST_SIZE`). `id` must be scalar; a missing `id`, a `jsonrpc` other than
`"2.0"`, or an object/array `id` is a `REQUEST` error. Call `capabilities` first:
it ships the method catalog and the state field descriptions.

## Methods

| Method | Purpose | Parameters |
| --- | --- | --- |
| `capabilities` | self-description: methods, state schema, notifications | none |
| `status` | **unified read-only state snapshot**; issues no device command | `include`, `backup_limit` |
| `doctor` | validate the hash-pinned resource pack | none |
| `connect_backup` | enter fastboot, then the **mandatory full backup** | `destination`, `enter` |
| `inspect` | read version and updater state from verified session snapshots | none |
| `keygen` | create a local RSA-2048 key pair (**never returns private key material**) | `directory` |
| `prepare` | build a reviewable image from verified backups; **no camera write** | `action`, `value`, `public_key_path`, `source_backup`, `partition` |
| `apply` | apply one reviewed plan and verify the full readback | `plan_id`, `confirmation` |
| `reboot` | leave fastboot; **invalidates this session's write permission** | none |

Parameters are a whitelist: an unknown key is a `PARAMS` error, never ignored.

## The state snapshot

`status` is the single answer to "what is the state right now". Pick sections with
`include` (default: all six — `session`, `resources`, `device`, `host`, `backups`).

```json
{
  "api_version": 1,
  "protocol": "jsonrpc-2.0-stdio",
  "toolkit_version": "0.2.0",
  "sections": ["backups", "device", "host", "resources", "session"],
  "state": "disconnected",
  "write_enabled": false,
  "backup": null,
  "partitions": [],
  "last_result": null,
  "session": {"connected": false, "fastboot": false, "backup_folder": null,
              "backup_complete": null, "device": null,
              "plans_pending": 0, "partitions_known": 0},
  "next_actions": ["doctor", "connect_backup"],
  "resources": {"ready": true, "errors": [], "distribution": "LOCAL-ONLY; …", "checked": true},
  "device": {"payload_usb_present": false, "bootsel_volumes": [], "observation_only": true},
  "host": {"platform": "macos", "data_dir": "…/A7M4Toolkit",
           "lock": {"path": "…/device-lock", "held_by_this_process": false, "note": "…"}},
  "backups": {"root": "…/backups", "count": 1, "returned": 1,
              "items": [{"folder": "…", "name": "…", "complete": true, "created": 1792874931.39,
                         "partitions": 17, "device": {...}, "readable": true}]}
}
```

- `state`, `write_enabled`, `backup`, `partitions` and `last_result` are the
  session fields older clients already used; `session` spells the same facts out.
- `write_enabled` only turns true after a **complete backup in this session**. That
  is a camera-side precondition, not advice: without it, write methods return
  `BACKUP_REQUIRED`.
- The `device` section is observation only — USB presence and mounted volumes, no
  device command. Polling it never moves the camera.
- `resources.ready` hashes the pack (~0.3 s). Ask for `{"include":["session"]}` when
  you only need session state.
- `host.lock.held_by_this_process` answers whether **this** process holds the lock.
  Another process holding it cannot be queried; it surfaces as `BUSY` on the write
  itself. That is the nature of advisory locking; the interface does not pretend
  otherwise.
- `backups.count` counts directories; `items` describes the newest `backup_limit`.

## Errors

`error.data.code` is the stable machine-readable code.

```json
{"jsonrpc":"2.0","id":7,"error":{"code":-32000,"message":"Unknown method",
 "data":{"code":"METHOD","hint":""}}}
```

| `data.code` | Meaning |
| --- | --- |
| `METHOD` | no such method (read `capabilities` first) |
| `PARAMS` | unknown or invalid parameter key or value |
| `REQUEST` | bad envelope (missing `id`, wrong `jsonrpc`, non-scalar `id`) |
| `REQUEST_SIZE` | request line over 1 MiB |
| `INVALID_REQUEST` | malformed JSON |
| `BACKUP_REQUIRED` | no complete backup in this session |
| `BUSY` | another process holds the device lock |
| `LOCK` / `LOCK_LOST` | not holding the lock / lost it while holding it |
| `ASSETS_MISSING` / `ASSET_INTEGRITY` / `ASSET_PERMISSION` | pack missing, hash mismatch, not executable |
| `USB_COMMUNICATION`, `DISK_SPACE`, `CONFIRMATION`, `INTERNAL`, … | see `a7m4/common.py` |

Anything except `INTERNAL` is a deterministic outcome you can branch on.

## Safety contract (unchanged)

- **No arbitrary command execution**: `arbitrary_commands: false`; the method table is
  the whole capability set.
- **`apply` is two-step**: `prepare` returns a `plan_id` and a confirmation string;
  `apply` must echo it exactly. Plans are bound to the session and die at `reboot`.
- **`keygen` never returns private key material**; keys are written under `data_dir`.
- **No TCP listener**: nothing is exposed on the network, and a session serves one client.

## Examples

```bash
a7m4 state | jq '.state, .resources.ready, .backups.count'

{ echo '{"jsonrpc":"2.0","id":1,"method":"capabilities"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"status","params":{"include":["session"]}}'
  echo '{"jsonrpc":"2.0","id":3,"method":"doctor"}'
} | a7m4 agent | jq -c '{id, ok:(.error==null), code:.error.data.code}'
```

## Boundaries

- Verified on macOS up to: pack readiness, session state, backup inventory, and the
  parameter/error contract. **Camera operations = 0**, so the real-device behaviour of
  `connect_backup` and `apply` remains unverified.
- The window and the agent use the same methods and the same guards.

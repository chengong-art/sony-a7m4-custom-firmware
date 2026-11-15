# macOS port

[中文](setup-macos.md) · [Back](../README.md)

This directory is a macOS adaptation of
[Erlla/sony-a7m4-custom-firmware](https://github.com/Erlla/sony-a7m4-custom-firmware)
at upstream HEAD `d3fa4c5a323e3868e06df840f3c27b941eec714f` (the initial port
pinned `0332a1fa…`; it tracked upstream on 2026-11-14 and 2026-11-15). The upstream host
toolkit targets Windows 10/11 only. Nothing here changes a firmware recipe,
address, hash pin or verification boundary.

All camera-side constraints (original battery, complete eMMC backup, the
self-made flashing cable, exact-hash baselines) are inherited unchanged. Read
the [upstream risk and backup requirements](../README.md) and the
[wiring notes](wiring.md) first.

## Graphical entry point (A7M4 ToolKit.app)

```bash
.venv/bin/python tools/make_app_macos.py      # writes A7M4 ToolKit.app to the repo root
open "A7M4 ToolKit.app"
```

The window is not a fake terminal; it is a **client of the agent interface**
([docs/agent-api.en.md](agent-api.en.md)). It starts an `a7m4 agent` child, asks for
`capabilities`, renders one button per method grouped by the method's own safety
level (read / key / camera-touching / write), sends the parameters you edit, and
shows the same progress notifications and results an agent receives. The header
refreshes `status` every three seconds.

Button captions are Chinese (the toolkit's audience), while the method id — the
contract name — is shown on the selection line, so the window and an agent
still send the identical request.

There is therefore **no prompt to answer**: methods take parameters instead of
asking questions, so nothing can sit waiting for a keystroke.

- The parameter box is exactly the JSON an agent would send; a mistake shows the
  server's error code (`PARAMS`, ...) verbatim.
- After a successful `prepare`, the `plan_id` and confirmation string are filled
  into the `apply` template — but `apply` still requires echoing them, and that is
  enforced **server-side**, so the window cannot bypass it.
- One request at a time (the session is single-threaded). Use "abort and restart
  session" to stop; the write permission dies with the session.
- The demo checkbox takes effect when the session restarts.

Four honest caveats: **unsigned** (fine locally, blocked by Gatekeeper elsewhere);
the **menu bar shows `python`** rather than the app name (script-execs-interpreter
bundle; cosmetic, and a real fix needs an embedded interpreter); the app must sit
beside `advanced-macos/`, `vendor/` and `.venv/`; and the bundle is git-ignored —
rebuild it with the command above.

## What changed, and why

| Area | Windows (upstream) | macOS (this port) | Where |
| --- | --- | --- | --- |
| fastboot host binary | `bin/fastboot.exe` | `bin/fastboot` from Android Platform Tools | same protocol; the reviewed logical name is still `bin/fastboot.exe` |
| USB driver | WinUSB INF via elevated `pnputil` | none needed: Apple's USB stack binds VID `18D1`/PID `4EE0` | `hardware.install_driver` reports `not-required-on-macos` instead of pretending to install |
| Device lock | Win32 named mutex | `flock(2)` on a per-user directory plus re-validation before each write, not under `A7M4_DATA_DIR` | any path-based lock can be defeated by deleting the path, so the write paths call `host.verify_device_lock()` and fail with `LOCK_LOST` |
| RP2040 BOOTSEL | drive letters | `/Volumes/*/INFO_UF2.TXT` | `host.bootsel_volumes` |
| Serial | `COMx` | `/dev/cu.usbmodem*` | pyserial enumeration by VID `2e8a` is unchanged |
| SquashFS | `sqfs2tar.exe`/`tar2sqfs.exe` | same 1.3.2 sources, macOS arm64 build | identical `-c gzip -b BLOCK [--exportable]` invocation |
| Entry points | `advanced/*.bat` | `advanced-macos/*.command` | same argument forwarding, `Exit code:` line and `A7M4_DEMO` semantics |
| State directory | `%LOCALAPPDATA%\A7M4Toolkit` | `~/Library/Application Support/A7M4Toolkit` | override with `A7M4_DATA_DIR` |

Deliberately **not** adapted: `feature_integration` pins a specific
`arm-none-eabi-gcc` binary and `jpeg_exif_dimensions` pins Zig 0.14.1 *Windows
x86_64*. Those are input-identity pins, not platform issues — substituting a
different compiler changes the baseline and would invalidate the whole
evidence chain. No hash check was relaxed to make anything run.

## Install

```bash
cd ports/erlla-a7m4-macos
python3.12 -m venv .venv          # any 3.11+ works; do not use the system 3.9/3.10
.venv/bin/python -m pip install pyserial pyelftools cryptography
.venv/bin/python -m pip install numpy "dissect.extfs" "dissect.util" pytest   # optional: offline firmware + tests
```

**Do not use the system `python3`**: it is commonly 3.9/3.10 on macOS, below the
`requires-python` 3.11 floor, and fails far from the cause (`hashlib.file_digest`).
`advanced-macos/_launch.sh` now probes the version, picking the first usable
`python3.13` -> `python3.12` -> `python3.11` -> `python3`, and exits 2 with
`Python 3.11+ not found` rather than running with an unusable interpreter.

Once installed, **double-click** the `.command` entries in `advanced-macos/`; no
shell is needed. Start with the environment check and confirm `ready: true`.

`pyproject.toml` is unchanged; runtime dependencies remain `pyserial==3.5`,
`pyelftools==0.32`, `cryptography==46.0.3`.

```text
<resource dir>/
  bin/fastboot
  bin/fastboot.bin
  bin/uart_boot.ino.uf2
  squashfs/sqfs2tar
  squashfs/tar2sqfs
  squashfs/libsquashfs.1.dylib
```

```bash
.venv/bin/python tools/register_resources.py <resource dir>
.venv/bin/python -m a7m4 doctor     # prints "platform": "macos"
```

The pack defaults to `vendor/` (git-ignored); point `A7M4_RESOURCES` elsewhere
if it lives outside the checkout.

**`libsquashfs.1.dylib` must be registered together with the two squashfs tools.**
This build links them against `@loader_path/libsquashfs.1.dylib`; registering
without it does not fail, but the first invocation dies with
`Library not loaded`. `register_resources.py` now copies `.dylib`/`.so` siblings
and keeps their mode bits — it previously accepted only `.exe/.dll/.bin/...`,
which is how the library went missing.

Recovery-only users need just `bin/fastboot`:

```bash
.venv/bin/python tools/register_resources.py <resource dir> --recovery-only
```

### Building the SquashFS tools

```bash
curl -L -o sqfs.tar.gz https://codeload.github.com/AgentD/squashfs-tools-ng/tar.gz/refs/tags/v1.3.2
tar xzf sqfs.tar.gz && cd squashfs-tools-ng-1.3.2
autoreconf --force --install --symlink -I /opt/homebrew/share/aclocal
CONFIG_SITE=/dev/null ./configure --without-selinux
make -j8
```

Two macOS-specific traps: Homebrew's `pkgconf` installs `pkg.m4` outside
`aclocal --print-ac-dir`, so the `-I` is required; and a source path containing
spaces (this project lives under `相机固件 逆向 Workspace`) breaks `configure`'s
site-script and libtool install paths, hence `CONFIG_SITE=/dev/null` plus a
space-free build directory. The tools reference `libsquashfs.1.dylib` through
`@loader_path`, so keep the dylib next to the binaries — the bundled
`tools/squashfs-ng-1.3.2-macos-arm64/` is already laid out that way.

## Run

Double-click any `.command` in `advanced-macos/`, or:

```bash
export A7M4_DATA_DIR="$PWD/../../tmp/a7m4-state"
advanced-macos/检查运行环境.command
```

Environment variables match the Windows launchers (`A7M4_PYTHON`, `A7M4_DEMO`,
`A7M4_NO_PAUSE`) plus `A7M4_RESOURCES` and `A7M4_DATA_DIR`. The recovery entry
point still refuses `A7M4_DEMO=1`, exactly like `_launch_recovery.cmd`.

## Verification status

Offline and synthetic only — no camera was connected, read or written:

- Upstream suite on the cloud macOS VM (26.6.2 arm64 / Python 3.12.16):
  **352 passed, 7 skipped** (the 7 skips are Windows-only entry points). The
  macOS tests cover asset aliasing, the execute bit (keyed on the logical name,
  not the suffix), lock exclusion
  (deleted target, a lock path that does not follow the data-dir override, a
  refused file target, a refused write without the lock), payload presence
  probing, BOOTSEL volume detection, the driver no-op, resource registration and
  `.command` argument forwarding.
- **Independent review**: this port was reviewed by a third-party model
  (Gemini 3.8 Flash) under two different briefs, returning `reject` and
  `accept-with-findings`. Both reports independently found the same blocking defect:
  the first directory-lock design could still be removed with `rmdir` while
  empty, silently dropping exclusion. That was correct, and the lock was
  reimplemented around re-validation immediately before each write, with four
  regression tests. Every other valid finding was also fixed (a weakened
  Windows DLL regression test, a tautological launcher assertion, `.command`
  scripts dropping their arguments, the execute-bit gate keyed on the suffix,
  and the missing macOS payload-presence check).
- **The third round (two independent reviewers) returned `reject` /
  `accept-with-findings`, and again the defect was in my own fix**: I had registered
  held locks in a process-global stack, so a thread that never acquired anything
  could pass `verify_device_lock()` as long as another thread held a lock (fail-open,
  reproduced). Worse, on the default path: after the lock directory was deleted and
  recreated, the stale holder verified the *replacement* holder's valid inode and
  missed its own `LOCK_LOST`, letting two ordinary Engine operations proceed. A
  narrow asynchronous-interruption window was also reproduced — `append` before the
  inner `try` left a stale, still-verifying registration. The lock registry is now
  **per thread** (a thread with no lock sees an empty stack and gets `LOCK`,
  fail-closed), the push happens inside the protected block, and nine lock
  regression tests pin it. **Correction**: at that commit this document claimed the
  four tests covered replacement-holder masking; the reviewer showed only the
  cross-thread borrow test existed, so the claim was wrong and the masking test has
  now genuinely been added. Currently pinned: a thread cannot borrow another's
  authority; a stale holder reports `LOCK_LOST` instead of validating the
  replacement holder's inode; a cross-thread close strands nothing; a released lock
  refuses to verify; line-by-line interruption leaves nothing that can authorise a
  write; an interruption at the `os.open` boundary leaks no descriptor; same-thread
  nesting unwinds; Windows recursion and exception cleanup; and a wrapper-construction
  failure still releases the kernel handle. Residual, recorded honestly: the gap between `verify` and the
  command, and deletion during a long flash, cannot be closed with advisory POSIX
  locking — `rm -rf` stays outside what this lock promises; only plain `rmdir` is
  blocked, by the marker file. Three further residuals are recorded the same way: an
  asynchronous interruption inside a cleanup block skips the rest of that block (so
  the implementation invalidates the lock object first, leaving survivors that refuse
  rather than authorise); Windows `WAIT_ABANDONED` transfers ownership without proving
  the previous holder's camera command finished; and the lock scope is per user
  (POSIX) / session-local (Windows), so other users or sessions are not excluded.
- **The second review round returned `reject`, and the regression it found came from
  my own fix**: the Windows branch yielded `None` without registering the held lock, so
  the re-validation I wired into the write paths made **every Windows flash fail
  unconditionally**. I reproduced it here with a stubbed kernel32 (`LOCK` raised while
  the mutex branch "held" the lock) and fixed it by registering a kernel handle — a
  named mutex is not a path and cannot be unlinked, so verification is a no-op for it —
  while still requiring the write to be inside `hardware_lock`. I did **not** take the
  reviewer's alternative of skipping the check on Windows, because that would drop the
  invariant that a destructive step must hold the lock. Lock bookkeeping also moved from
  one global to a process-level stack (a Win32 mutex is recursively acquirable, so an
  inner release would otherwise clear the outer holder). Also fixed: tests colliding on
  the real shared lock, inconsistent re-validation at the two `reboot` sites, and the
  native name `bin/fastboot` bypassing the execute-bit gate.
  **"Windows behaviour is unchanged" was therefore briefly false because of my own
  fix**; it is now pinned by `test_windows_lock_still_lets_a_write_through` and the other
  stub tests. The Windows branch has still never run on real Windows, and that stands.
- `doctor` returns `ready: true` against a real registered pack; the `--demo`
  backup and maintenance flows run end to end.
- `tar2sqfs`/`sqfs2tar` round-tripped a synthetic tar on this machine
  (`hsqs`, gzip, v4.0, block size and `--exportable` all as configured).
- `fastboot devices` returns an empty list with exit code 0, confirming the
  binary runs without a driver.

Still unverified without hardware and explicit authorization: USB enumeration
of the payload device, the RP2040 serial handshake, real partition reads and
writes, `oem partition dump` chunk timing and post-write readback. None of those
depend on the host OS, and none were performed here.

## Test keypair policy

Upstream `keys/published/` publishes an RSA-2048 keypair whose private half is
public by design: it exists so a self-signed test package is accepted by a
camera and carries **no identity guarantee** — anyone can sign with it. The
maintainer explicitly authorised publishing it, so this port **commits the pair
exactly as upstream does**:

- `.gitignore` un-ignores those two paths only; every other `*.pem` stays blocked.
- Fingerprints are pinned in `keys/published/fingerprints.json`, and
  `tools/check_public_tree.py` verifies the pair, the public DER SHA-256 and a
  sign/verify round trip on each run, reporting `published_key_pair_verified`.
- The files are tracked, so their absence is a real defect: `conftest.py` aborts
  the session at collection time instead of skipping the dependent test.

When signing a real SD package for your own camera, **still generate your own
key**: leave the PEM path blank in the `maintain` menu and it creates one; keep
the private half offline. Signing with the published key is equivalent to not
signing at all, because anyone can produce an identically signed package.

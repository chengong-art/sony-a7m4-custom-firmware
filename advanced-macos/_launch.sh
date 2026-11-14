#!/bin/bash
# Shared launcher for the macOS entry points. Mirrors advanced/_launch.cmd:
# arguments are forwarded verbatim; no argument is ever turned into shell text.
#
# Environment (same names as the Windows launchers where the meaning matches):
#   A7M4_PYTHON     explicit interpreter path
#   A7M4_DEMO=1     synthetic camera instead of hardware
#   A7M4_NO_PAUSE=1 never wait for Return (default waits when interactive)
#   A7M4_RESOURCES  registered local resource pack (default: <repo>/vendor)
#   A7M4_DATA_DIR   backups/plans/keys location
#
# Compatible with the /bin/bash 3.2 that ships with macOS.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 2

DEMO_FLAG=""
if [ "${A7M4_DEMO:-0}" = "1" ]; then
  DEMO_FLAG="--demo"
fi

PYTHON=""
python_ok() {
  # requires-python is >=3.11. A stock macOS `python3` is often 3.9/3.10, which
  # fails much later with something unrelated (hashlib.file_digest), so the
  # version is checked here where the message can be actionable.
  [ -x "$1" ] && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}
for candidate in \
  "${A7M4_PYTHON:-}" \
  "$ROOT/.venv/bin/python" \
  "$ROOT/venv/bin/python" \
  python3.13 python3.12 python3.11 python3
do
  [ -n "$candidate" ] || continue
  if [ -x "$candidate" ]; then
    resolved="$candidate"
  else
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
  fi
  [ -n "$resolved" ] || continue
  if python_ok "$resolved"; then
    PYTHON="$resolved"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ not found. Create .venv or set A7M4_PYTHON." >&2
  echo "  python3.12 -m venv .venv && .venv/bin/python -m pip install pyserial pyelftools cryptography" >&2
  result=2
elif [ "${1:-}" = "__recovery__" ] && [ -n "$DEMO_FLAG" ]; then
  # Recovery commands the camera by definition; a demo flag must not be read as
  # "synthetic device here" and silently change what the entry point does.
  echo "Recovery does not use A7M4_DEMO. Run the offline tests instead." >&2
  result=2
elif [ "${1:-}" = "__recovery__" ]; then
  shift
  "$PYTHON" -B -X utf8 -m a7m4.recover_sd "$@"
  result=$?
elif [ -n "$DEMO_FLAG" ]; then
  "$PYTHON" -B -X utf8 -m a7m4 "$DEMO_FLAG" "$@"
  result=$?
else
  "$PYTHON" -B -X utf8 -m a7m4 "$@"
  result=$?
fi

echo
echo "Exit code: $result"
if [ "${A7M4_NO_PAUSE:-0}" != "1" ] && [ -t 0 ]; then
  printf 'Press Return to close / 按回车关闭 '
  read -r _ || true
fi
exit "$result"

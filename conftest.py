"""Port-local test configuration.

The published maintainer keypair *is* committed here, matching upstream: the
maintainer explicitly authorised publishing that experimental signing identity
and the private half is intentionally public. `.gitignore` allows exactly those
two paths and blocks every other `*.pem`, and
`tools/check_public_tree.py` verifies their fingerprints on each run.

Because the pair is now part of the tree, its absence is a real defect rather
than an environment choice — so this module fails the session loudly instead of
skipping the dependent test.

Windows-only entry points (BAT launchers) are still skipped on POSIX hosts by
the upstream markers; the macOS shell launchers are covered by
`tests/test_macos_launchers.py`.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYS = ROOT / "keys/published"
PRIVATE_KEY = KEYS / "A7M4_updater_private.pem"
PUBLIC_KEY = KEYS / "A7M4_updater_public.pem"


def pytest_configure(config):
    missing = [p.name for p in (PRIVATE_KEY, PUBLIC_KEY) if not p.is_file()]
    if missing:
        raise RuntimeError(
            "Committed test keypair is missing: " + ", ".join(missing) +
            ". These files are tracked; restore them from Git rather than "
            "skipping tests that depend on them.")

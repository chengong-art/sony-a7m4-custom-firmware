#!/bin/bash
# Read-only fastboot check: no partition read, write or reboot.
exec "$(dirname "$0")/_launch.sh" __recovery__ --check-connection "$@"

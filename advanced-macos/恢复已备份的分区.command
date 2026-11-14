#!/bin/bash
# Restore a partition from a complete same-camera backup.
exec "$(dirname "$0")/_launch.sh" maintain --action restore "$@"

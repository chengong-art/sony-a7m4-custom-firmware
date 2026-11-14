#!/bin/bash
# Restore the official updater verification key.
exec "$(dirname "$0")/_launch.sh" maintain --action restore_official "$@"

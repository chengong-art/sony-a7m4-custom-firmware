#!/bin/bash
# Build the self-signed updater from this camera backup.
exec "$(dirname "$0")/_launch.sh" maintain --action unlock "$@"

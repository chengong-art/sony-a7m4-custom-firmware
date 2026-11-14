#!/bin/bash
# Validate the local resource pack; no device access.
exec "$(dirname "$0")/_launch.sh" doctor "$@"

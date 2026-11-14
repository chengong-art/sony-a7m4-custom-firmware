#!/bin/bash
# SD-card recovery trigger; camera must already be in fastboot.
exec "$(dirname "$0")/_launch.sh" __recovery__ "$@"

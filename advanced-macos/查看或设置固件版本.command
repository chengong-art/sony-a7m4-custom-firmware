#!/bin/bash
# View or set the reported firmware version.
exec "$(dirname "$0")/_launch.sh" maintain --action set_version "$@"

#!/bin/bash
# Full partition backup; acquired before any write.
exec "$(dirname "$0")/_launch.sh" maintain --action backup "$@"

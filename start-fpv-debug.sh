#!/bin/bash
# Quick launcher for FPV Debug Cockpit
unset VIRTUAL_ENV
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/fpv-debug/start.sh"

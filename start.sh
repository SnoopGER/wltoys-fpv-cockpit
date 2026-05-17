#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  🏎️  WLtoys FPV Debug Cockpit — Launcher
# ═══════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Deactivate any virtualenv so we use system python
unset VIRTUAL_ENV
export PATH="/usr/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

PYTHON="python3"

# Verify flask is available
if ! "$PYTHON" -c "import flask" 2>/dev/null; then
    echo "[!] Flask not found. Installing..."
    "$PYTHON" -m pip install --user flask Pillow 2>&1
    if ! "$PYTHON" -c "import flask" 2>/dev/null; then
        echo "[ERROR] Flask install failed. Try manually:"
        echo "  pip install flask Pillow"
        echo "  or: sudo apt install python3-flask"
        exit 1
    fi
fi

LAN_IP=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1)
PORT=5555

# Kill any existing instance
pkill -f "python3.*webapp.py" 2>/dev/null
sleep 1

echo ""
echo "  ╔═══════════════════════════════════════════════════╗"
echo "  ║  🏎️  WLTOYS 6405 FPV DEBUG COCKPIT              ║"
echo "  ╠═══════════════════════════════════════════════════╣"
echo "  ║                                                   ║"
echo "  ║  Local:  http://localhost:${PORT}                  ║"
echo "  ║  LAN:    http://${LAN_IP}:${PORT}                 ║"
echo "  ║                                                   ║"
echo "  ║  Ctrl+C to stop                                   ║"
echo "  ╚═══════════════════════════════════════════════════╝"
echo ""

"$PYTHON" webapp.py

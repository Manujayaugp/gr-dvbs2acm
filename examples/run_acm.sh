#!/usr/bin/env bash
# run_acm.sh — Clean launcher for DVB-S2 ACM loopback simulation
#
# Use this instead of GRC Companion's Execute button.
# Generates fresh Python from the GRC, strips the noisy message_debug
# block automatically (GRC always re-adds it; this removes it every run),
# clears stale Python cache, then starts the flowgraph.
#
# Usage:
#   cd .../gr-dvbs2acm/examples
#   ./run_acm.sh

set -e
cd "$(dirname "$0")"

echo "[run_acm] Generating Python from GRC..."
grcc acm_loopback.grc 2>&1 | grep -v "^$\|Block paths\|Loading:\|>>>" || true

echo "[run_acm] Patching out message_debug (raw PMT spam)..."
python3 - <<'PY'
import re
with open('acm_loopback.py', 'r') as f:
    code = f.read()

before = code.count('message_debug')

# Remove block instantiation line
code = re.sub(r'[ \t]*self\.message_debug\s*=\s*blocks\.message_debug[^\n]*\n', '', code)
# Remove every msg_connect line that references message_debug
code = re.sub(r'[ \t]*self\.msg_connect\(\([^)]*\),\s*\([^)]*message_debug[^)]*\)\)\n', '', code)

after = code.count('message_debug')
with open('acm_loopback.py', 'w') as f:
    f.write(code)
removed = before - after
print(f"[run_acm] Removed {removed} message_debug reference(s) — console is clean.")
PY

echo "[run_acm] Clearing Python cache..."
find ../python/dvbs2acm/__pycache__ -name "*.pyc" -delete 2>/dev/null || true

echo "[run_acm] Starting DVB-S2 ACM loopback..."
echo ""
exec python3 -u acm_loopback.py "$@"

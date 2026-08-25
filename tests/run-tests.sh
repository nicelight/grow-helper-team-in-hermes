#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${GROWHELPER_PYTHON:-python3}"
cd "$ROOT"
"$PYTHON_BIN" -m compileall -q plugin scripts tests
"$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py' -v
if command -v node >/dev/null 2>&1; then
  node --check plugin/grow-helper-monitor/dashboard/dist/index.js
fi
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
import yaml
root = Path('.')
for path in root.rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
for path in (root/'team.yaml', root/'plugin/grow-helper-monitor/plugin.yaml', root/'config/models.example.yaml'):
    yaml.safe_load(path.read_text(encoding='utf-8'))
print('JSON/YAML validation: OK')
PY

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUT="${1:-$ROOT/dist}"
NAME="grow-helper-team-$VERSION"
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
    if 'dist' not in path.parts:
        json.loads(path.read_text(encoding='utf-8'))
for path in (
    root/'team.yaml', root/'plugin/grow-helper-monitor/plugin.yaml',
    root/'config/models.example.yaml',
):
    yaml.safe_load(path.read_text(encoding='utf-8'))
print('Release JSON/YAML validation: OK')
PY

rm -rf "$OUT"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/$NAME"
mkdir -p "$STAGE"

# Copy only release sources; never carry local caches or previous artifacts.
(
  cd "$ROOT"
  tar \
    --exclude='./dist' \
    --exclude='./__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='./.pytest_cache' \
    --exclude='./MANIFEST.sha256' \
    -cf - .
) | (cd "$STAGE" && tar -xf -)

(
  cd "$STAGE"
  find . -type f ! -name MANIFEST.sha256 -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > MANIFEST.sha256
)

# Deterministic metadata where GNU tar supports it.
tar --sort=name --mtime='UTC 2026-08-19 00:00:00' --owner=0 --group=0 --numeric-owner \
  -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
(
  cd "$OUT"
  sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256"
)

printf '%s\n' "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256"

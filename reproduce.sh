#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
if [ -n "${DDN_PYTHON:-}" ]; then
  REPRO_PYTHON="$DDN_PYTHON"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  REPRO_PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  REPRO_PYTHON="$(command -v python3.12)"
else
  echo 'Python 3.12 is required. See README.md.' >&2
  exit 2
fi
if ! "$REPRO_PYTHON" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  echo 'Use Python 3.12. Set DDN_PYTHON or create .venv. See README.md.' >&2
  exit 2
fi
REPRO_MODE="${1:-help}"
if [ "$#" -gt 0 ]; then shift; fi
case "$REPRO_MODE" in
  live) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/live.py" "$@" ;;
  result) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/result.py" "$@" ;;
  image-assets) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/prepare_image.py" "$@" ;;
  tables) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/rebuild.py" "$@" ;;
  verify) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/verify.py" "$@" ;;
  restore) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/restore.py" "$@" ;;
  check) exec "$REPRO_PYTHON" -B "$REPO_ROOT/reproduction/check.py" "$@" ;;
  help|-h|--help) echo 'Usage: ./reproduce.sh {result [--output NEW_DIR]|tables|verify|restore [--from DIR] [--seed]|image-assets [--from DIR]|check [--live]}' ;;
  *) echo "Unknown mode: $REPRO_MODE. Use tables, verify, restore or check." >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# All tests in one go, for CI and locally. Needs no real Shelly devices:
# tests/fake_shelly.py simulates the fleet.
#
# Usage: bash tests/ci.sh     (from the repository root or from anywhere)
#   CI_VENV=/path              use an existing venv instead of creating a new one
set -euo pipefail

cd "$(dirname "$0")/.."
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n=== %s ===\n' "$*"; }

step "Python environment"
if [ -n "${CI_VENV:-}" ]; then
  PY="$CI_VENV/bin/python"
else
  python3 -m venv "$WORK/venv"
  PY="$WORK/venv/bin/python"
  "$PY" -m pip install -q --disable-pip-version-check -r requirements.txt ruff
fi
"$PY" --version

step "ruff (errors, no style questions)"
"$PY" -m ruff check --select F,E9 app tests tools

step "Default policy parses"
"$PY" -c "import app.policy as p; p.parse(open(p.DEFAULT_POLICY_PATH, encoding='utf-8').read()); print('ok')"

step "test_core.py"
"$PY" tests/test_core.py

step "test_api.py"
"$PY" tests/test_api.py

step "test_profiles.py"
"$PY" tests/test_profiles.py

step "test_apply.py"
"$PY" tests/test_apply.py

step "test_notify.py"
"$PY" tests/test_notify.py

step "test_oidc.py"
"$PY" tests/test_oidc.py

step "test_vendor.py"
"$PY" tests/test_vendor.py

step "ALL GREEN"

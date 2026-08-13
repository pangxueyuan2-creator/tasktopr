#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cp -R "$ROOT_DIR/demo/zero_division_repo/." "$WORK_DIR/"
cd "$WORK_DIR"
git init -b main >/dev/null
git config user.name "TaskToPR Demo"
git config user.email "demo@example.invalid"
git add .
git commit -m "chore: add zero division demo" >/dev/null

printf '\n== TaskToPR demo: Issue #1 → planned, patched, tested local branch ==\n\n'
tasktopr fix 1 --demo --no-pr

printf '\n== Verified diff ==\n\n'
git diff -- calculator.py test_calculator.py
printf '\n== Verified tests ==\n\n'
python -m unittest discover -v

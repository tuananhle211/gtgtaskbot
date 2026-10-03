#!/usr/bin/env bash
# Run every quality gate the same way CI would.
#
# The virtualenv is placed outside the source tree on purpose: this project is
# usually edited over SSHFS, where a .venv would be painfully slow and would
# bloat the mount. Override with UV_PROJECT_ENVIRONMENT if you prefer.
set -euo pipefail

cd "$(dirname "$0")/.."

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.cache/meobot-venv}"
export MYPY_CACHE_DIR="${MYPY_CACHE_DIR:-$HOME/.cache/meobot-mypy}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

step "uv sync --frozen"
uv sync --frozen

step "ruff check"
uv run ruff check .

step "ruff format --check"
uv run ruff format --check .

step "mypy src"
uv run mypy src

step "pytest"
uv run pytest

printf '\n\033[1;32mAll quality gates passed.\033[0m\n'

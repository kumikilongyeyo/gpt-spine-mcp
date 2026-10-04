#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
uv tool install --force "$repo_dir[agents]"

if [ "${1:-}" = "--codex" ]; then
  if ! command -v codex >/dev/null 2>&1; then
    echo "Installed GPT Spine, but codex was not found on PATH." >&2
    exit 1
  fi
  codex mcp remove gpt-spine >/dev/null 2>&1 || true
  codex mcp add gpt-spine -- gpt-spine-mcp
fi

gpt-spine doctor

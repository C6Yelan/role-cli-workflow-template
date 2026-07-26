#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s <project-root>\n' "$0" >&2
  exit 2
fi

root="$(realpath -- "$1")"
codex-role-workflow doctor "$root"
codex-role-workflow open "$root"
codex-role-workflow verify "$root"
codex-role-workflow status "$root"
codex-role-workflow stop "$root"

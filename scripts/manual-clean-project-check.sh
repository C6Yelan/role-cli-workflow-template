#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s <project-root>\n' "$0" >&2
  exit 2
fi

root="$(realpath -- "$1")"
role-cli-workflow doctor "$root"
role-cli-workflow open "$root"
role-cli-workflow verify "$root"
role-cli-workflow status "$root"
role-cli-workflow stop "$root"

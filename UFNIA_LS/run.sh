#!/usr/bin/env bash
set -euo pipefail
ufnia_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$ufnia_dir/run.py" "$@"

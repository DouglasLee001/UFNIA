#!/usr/bin/env bash
set -euo pipefail
ufnia_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$ufnia_root/UFNIA_LS/run.py" "$@"

#!/usr/bin/env bash
set -eu
ROUTE_CODE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROUTE_PYTHON="${PYTHON:-python3.12}"
"$ROUTE_PYTHON" "$ROUTE_CODE/fetch_upstream.py"
"$ROUTE_PYTHON" -m pip install --target "$ROUTE_CODE/vendor" --no-cache-dir -r "$ROUTE_CODE/requirements-runtime.lock"

#!/usr/bin/env bash
# Launch the retained best strategy on every case in the SAT benchmark collection.
set -euo pipefail

ufnia_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ufnia_root="$(cd -- "$ufnia_script_dir/.." && pwd)"
ufnia_python=/usr/bin/python3.12
ufnia_benchmarks=/data/shared/lbh/QF_UFNIA/sat/QF_UFNIA
ufnia_profile="$ufnia_root/result/ufnia_search/mature_v12_memory_profile.csv"
ufnia_dry_run=0

if (( $# > 1 )); then
  printf 'Usage: %s [--dry-run]\n' "$0" >&2
  exit 2
fi
case "${1:-}" in
  "") ;;
  --dry-run) ufnia_dry_run=1 ;;
  -h|--help)
    printf 'Usage: %s [--dry-run]\n' "$0"
    printf 'Run on node3: all SAT benchmarks, up to 180 workers, 600 seconds per case.\n'
    printf 'Starts in the background and prints its PID and output directory.\n'
    exit 0
    ;;
  *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac

if [[ "$(hostname -s)" != node3 ]]; then
  printf 'Run this script on node3: ssh node3\n' >&2
  exit 1
fi
[[ -x "$ufnia_python" ]] || { printf 'Missing Python: %s\n' "$ufnia_python" >&2; exit 1; }
[[ -x "$ufnia_root/build/ufnia_ls/ufnia_mature" ]] || {
  printf 'Build first: make -C %s/UFNIA_LS mature\n' "$ufnia_root" >&2
  exit 1
}
[[ -f "$ufnia_script_dir/run_ufnia_experiments.py" ]] || {
  printf 'Missing benchmark runner.\n' >&2; exit 1;
}
[[ -d "$ufnia_benchmarks" ]] || { printf 'Missing benchmarks: %s\n' "$ufnia_benchmarks" >&2; exit 1; }
[[ -r "$ufnia_profile" ]] || { printf 'Missing memory profile: %s\n' "$ufnia_profile" >&2; exit 1; }
command -v flock >/dev/null || { printf 'Missing command: flock\n' >&2; exit 1; }

if (( ufnia_dry_run )); then
  ufnia_output="$ufnia_root/result/best_node3_all_600_DRY_RUN"
else
  mkdir -p "$ufnia_root/result"
  exec 9>"$ufnia_root/result/.best_node3_all.lock"
  if ! flock -n 9; then
    printf 'A full run started by this script is already running on node3.\n' >&2
    exit 1
  fi
  ufnia_stamp="$(TZ=Asia/Shanghai date +%Y%m%d_%H%M%S)"
  ufnia_output="$(mktemp -d "$ufnia_root/result/best_node3_all_600_${ufnia_stamp}_XXXXXX")"
fi

ufnia_command=(
  env UFNIA_AFFINE_VIEWS=1 UFNIA_AFFINE_FOOTPRINT_CACHE=1
  UFNIA_AFFINE_PROGRESS=1 PYTHONDONTWRITEBYTECODE=1
  "$ufnia_python" -B "$ufnia_script_dir/run_ufnia_experiments.py"
  --solver-root "$ufnia_root"
  --benchmarks "$ufnia_benchmarks"
  --output "$ufnia_output"
  --require-host node3 --jobs 180 --timeout 600 --limit 0
  --memory-profile "$ufnia_profile" --memory-reserve-gb 24 --order memory
  --solver-args '["--backend","mature","--uf-observers","--interaction","joint","--scoring","paws","--seed","0","--preprocess-timeout","600.0"]'
)

if (( ufnia_dry_run )); then
  printf '%q ' "${ufnia_command[@]}"
  printf '\n'
  exit 0
fi

cd -- "$ufnia_root"
# FD 9 is inherited by the controller, keeping the lock until the run ends.
nohup "${ufnia_command[@]}" 9>&9 >"$ufnia_output/launcher.log" 2>&1 < /dev/null &
ufnia_pid=$!
printf '%s\n' "$ufnia_pid" >"$ufnia_output/launcher.pid"
printf 'PID: %s\nOutput: %s\nResults: %s/run-*/\nLog: %s/launcher.log\n' \
  "$ufnia_pid" "$ufnia_output" "$ufnia_output" "$ufnia_output"

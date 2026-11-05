#!/usr/bin/env python3
"""Reproduce the fixed SQL campaign, skipping known UNSAT configurations."""
import argparse
import json
from pathlib import Path
import resource
import sys
sys.dont_write_bytecode=True
CODE=Path(__file__).resolve().parent
sys.path[:0]=[str(CODE/"vendor"),str(CODE)]
from generate import deterministic_process,generate_selected

BASE=("orders_amount_commute","orders_amount_overcharge","calcite_77","calcite_149",
      "calcite_211","calcite_244","calcite_262","calcite_264","calcite_281","leetcode_1715")
EXTENDED=("orders_amount_commute","orders_amount_overcharge","calcite_149","calcite_244","leetcode_1715")
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=CODE.parent/"instance")
    parser.add_argument("--timeout-ms",type=int,default=60000)
    args=parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    source=CODE/"campaign-inputs.json";tasks=json.loads(source.read_text())
    records=generate_selected([t for t in tasks if t["task_id"] in BASE],[2],args.output,source,args.timeout_ms)
    records+=generate_selected([t for t in tasks if t["task_id"] in EXTENDED],[3,4],args.output,source,args.timeout_ms)
    return 1 if any(r["result"]=="error" for r in records) else 0
if __name__=="__main__":
    deterministic_process()
    raise SystemExit(main())

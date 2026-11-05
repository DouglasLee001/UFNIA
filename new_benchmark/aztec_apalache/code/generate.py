#!/usr/bin/env python3
"""Generate certified source-derived Aztec slashing SAT QF_UFNIA instances."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import tempfile
import slashing_model as model
from smt_audit import audit_snapshot, assert_serialization_matches

CODE=Path(__file__).resolve().parent
DEFAULT_OUTPUT=CODE.parent/"instance"
PROFILES={}
for kind,sizes in (("quorum",(2,3,4,6,8,12,16,24,32,48,64)),("executed",(2,4,8,12,16,24,32))):
    for signals in sizes:
        name=f"sp_{kind}_k{signals}"
        PROFILES[name]={"kind":kind,"signals":signals,"qmin":signals,"name":name}
PROFILES["sp_executed_k4_veto"]={"kind":"executed","signals":4,"qmin":4,"vetoes":1,"name":"sp_executed_k4_veto"}
DEFAULT_CASES=('sp_quorum_k12', 'sp_quorum_k16', 'sp_quorum_k24', 'sp_quorum_k32', 'sp_executed_k12', 'sp_executed_k16')

def _under_tmp(path):
    path=Path(path).resolve()
    if not path.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Alternate outputs and proof directories must be under /tmp")
    return path

def _run(output,selected,proof_dir):
    report={"certified_sat":[],"written":[],"preserved":[]}
    for name in selected:
        case=model.build_case(PROFILES[name])
        base=model.snapshot_text(case)
        assert_serialization_matches(base,case.assertions)
        audit=audit_snapshot(base)
        if audit["result"]!="pass" or not audit["research_eligible"]:
            raise ValueError("Not eligible standalone QF_UFNIA: "+name)
        certificate=model.certificate_parameters(case)
        validation=model.verify_certificate(case,certificate)
        if (validation.get("result")!="pass" or
            validation.get("checked_assertions")!=len(case.assertions) or
            validation.get("failed_assertion_indices") or
            validation.get("unresolved_assertion_indices")):
            raise ValueError("Complete SAT valuation failed: "+name)
        replay=model.replay_certificate(case,certificate)
        if replay.get("result")!="pass":
            raise ValueError("Source transition replay failed: "+name)
        header=("; Aztec/Apalache source-derived bounded slashing witness\n"
                "; classification: application_derived; semantic Python/Z3 port\n"
                "; source: konnov/aztec-governance-formal-verification-2025q3\n"
                "; source commit: 6fee2e4f6dbbe632cf62b411e344e1a008642cd7\n"
                "; source file: spec/slashing_proposer_machine.qnt\n"
                "; profile: "+name+"\n"
                "; SAT certified by complete total-UF valuation and source replay\n"
                "; certificate assignments are not constraints in this formula\n")
        text=header+base.replace("(set-logic QF_UFNIA)\n",
            '(set-logic QF_UFNIA)\n(set-info :status sat)\n',1)
        assert_serialization_matches(text,case.assertions)
        digest=hashlib.sha256(text.encode()).hexdigest()
        evidence={"profile":PROFILES[name],"formula_sha256":digest,
                  "formula_bytes":len(text.encode()),"metadata":case.metadata,
                  "audit":audit,"valuation":certificate,"validation":validation,"replay":replay}
        proof_dir.mkdir(parents=True,exist_ok=True)
        (proof_dir/(name+".json")).write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n")
        output.mkdir(parents=True,exist_ok=True)
        destination=output/("aztec_"+name+".smt2")
        if destination.exists():
            if destination.read_text()!=text:
                raise ValueError("Refusing to overwrite a different formula: "+str(destination))
            report["preserved"].append(destination.name)
        else:
            with tempfile.NamedTemporaryFile(mode="w",dir=output,prefix=".aztec-publish-",delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(text)
            try:
                temporary.chmod(0o644)
                try:
                    os.link(temporary,destination)
                except FileExistsError:
                    if destination.read_text()!=text:
                        raise ValueError("Refusing to overwrite a concurrent different formula: "+str(destination))
                    report["preserved"].append(destination.name)
                else:
                    report["written"].append(destination.name)
            finally:
                temporary.unlink(missing_ok=True)
        report["certified_sat"].append(name)
    return report

def generate(output=None,selected=None,proof_dir=None):
    output=DEFAULT_OUTPUT.resolve() if output is None else Path(output).resolve()
    if output!=DEFAULT_OUTPUT.resolve():
        output=_under_tmp(output)
    selected=list(DEFAULT_CASES if selected is None else selected)
    if not selected or any(name not in PROFILES for name in selected):
        raise ValueError("Choose one or more listed profiles")
    if len(set(selected))!=len(selected):
        raise ValueError("Repeated profile")
    if proof_dir is not None:
        return _run(output,selected,_under_tmp(proof_dir))
    with tempfile.TemporaryDirectory(prefix="ufnia-apalache-proofs-",dir="/tmp") as directory:
        return _run(output,selected,Path(directory))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list",action="store_true")
    parser.add_argument("--case",action="append",choices=sorted(PROFILES))
    parser.add_argument("--output",type=Path)
    parser.add_argument("--proof-dir",type=Path)
    args=parser.parse_args()
    if args.list:
        print("\n".join(PROFILES))
        return 0
    print(json.dumps(generate(args.output,args.case,args.proof_dir),sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())

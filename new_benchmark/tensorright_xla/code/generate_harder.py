#!/usr/bin/env python3
"""Certificate-first application-derived compositions of fixed TensorRight operations.

A final-stage naive_mirror mutation is checked across a shared output tile.
The original input UF, all shapes and shared symbolic strides occur in the
whole negated property. Both outputs are legal, equal-shaped and source-backed.
Native Haskell TensorRight/production HLO are not executed. No solver.check is
used: publication requires a complete explicit SAT valuation and independent
Python coordinate replay. Certificate values never become formula constraints.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile

import z3

CODE = Path(__file__).resolve().parent
DEFAULT_CONFIGS = ((2, 2, 2), (3, 3, 2))
EXTRA_CONFIGS = ((2, 3, 3), (4, 4, 3))
SOURCE_COMMIT = "b3e3a533ff67568db127783cc353ebe04142929f"
SOURCE_REPOSITORY = "https://github.com/ADAPT-uiuc/TensorRight"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


g = _load("tensorright_harder_base", CODE / "generate.py")
c = _load("tensorright_harder_concrete", CODE / "concrete_replay.py")
_checker = _load("tensorright_harder_checker", CODE / "common/smt_tools.py")


def case_name(rank, depth, lanes):
    return f"coupled_mirror_rank{rank}_depth{depth}_lanes{lanes}"


def build_case(rank, depth, lanes):
 if not (1 <= rank <= 4 and 2 <= depth <= 4 and 2 <= lanes <= 3):
  raise ValueError("Supported profiles require rank 1..4, depth 2..4 and lanes 2..3")
 parameters={}
 def vector(name):
  result=[z3.Int(f'{name}_{axis}') for axis in range(rank)]
  parameters.update({str(value):value for value in result})
  return result
 n=vector('extent');step=vector('shared_stride')
 base=vector('tile_base');jump=vector('tile_delta')
 pre=[z3.And(*(v>=2 for v in n)),z3.And(*(v>=2 for v in step)),
      z3.And(*(v>=1 for v in jump))]
 lhs=g.tensor('A',n);rhs=g.tensor('A',n)
 ml=g.Tensor(tuple(n),lhs.valid,lambda ix:z3.BoolVal(True))
 mr=g.Tensor(tuple(n),rhs.valid,lambda ix:z3.BoolVal(True))
 stages=[]
 for stage in range(depth):
  low=vector(f'low{stage}');high=vector(f'high{stage}')
  interior=vector(f'interior{stage}');start=vector(f'start{stage}');end=vector(f'end{stage}')
  value=z3.Int(f'padding_value{stage}');parameters[str(value)]=value
  pre += [z3.And(*(v>=1 for v in interior))]
  def joined(t):
   return g.concat_tensor(t,g.reverse_tensor(t,[0]),0)
  pl=g.pad_tensor(joined(lhs),low,high,interior,value)
  pr=g.pad_tensor(joined(rhs),low,high,interior,value)
  pml=g.pad_tensor(joined(ml),low,high,interior,z3.BoolVal(False))
  pmr=g.pad_tensor(joined(mr),low,high,interior,z3.BoolVal(False))
  pre += [z3.And(*(z3.And(s>=0,s<e,e<=size) for s,e,size in zip(start,end,pl.shape)))]
  lhs=g.slice_tensor(g.reverse_tensor(pl,range(rank)),start,end,step)
  ml=g.slice_tensor(g.reverse_tensor(pml,range(rank)),start,end,step)
  if stage==depth-1:
   rstart=[size-e for size,e in zip(pr.shape,end)]
   rmstart=[size-e for size,e in zip(pmr.shape,end)]
  else:
   rstart=[size-s-(p*((e-s-1)/p)+1) for size,s,e,p in zip(pr.shape,start,end,step)]
   rmstart=[size-s-(p*((e-s-1)/p)+1) for size,s,e,p in zip(pmr.shape,start,end,step)]
  rend=[size-s for size,s in zip(pr.shape,start)]
  rmend=[size-s for size,s in zip(pmr.shape,start)]
  rhs=g.reverse_tensor(g.slice_tensor(pr,rstart,rend,step),range(rank))
  mr=g.reverse_tensor(g.slice_tensor(pmr,rmstart,rmend,step),range(rank))
  stages.append((low,high,interior,start,end,value))
 indices=[[x+lane*d for x,d in zip(base,jump)] for lane in range(lanes)]
 equal_shape=z3.And(*(a==b for a,b in zip(lhs.shape,rhs.shape)))
 pre += [lhs.valid,rhs.valid,equal_shape]
 for index in indices:
  pre += [g.in_bounds(index,lhs.shape),g.in_bounds(index,rhs.shape),ml.read(index),mr.read(index)]
 good=z3.And(rhs.valid,equal_shape,*[
  z3.And(g.in_bounds(index,rhs.shape),lhs.read(index)==rhs.read(index)) for index in indices])
 return {'name':case_name(rank,depth,lanes),'rank':rank,'depth':depth,'lanes':lanes,'parameters':parameters,'lhs':lhs,'rhs':rhs,
         'indices':indices,'assertions':pre+[z3.Not(good)]}



def snapshot_text(case):
    """Serialize and extract the entire formula; never invoke check-sat."""
    emitter = z3.Solver()
    emitter.add(*case["assertions"])
    raw = "(set-logic QF_UFNIA)\n" + emitter.to_smt2() + "\n(exit)\n"
    queries = _checker.extract_queries(raw)
    if len(queries) != 1:
        raise ValueError("Expected one complete harder-model query")
    return queries[0]["text"]


def assertion_fingerprint(text):
    encoded = "\n".join(a.sexpr() for a in z3.parse_smt2_string(text)).encode()
    return hashlib.sha256(encoded).hexdigest()


def certificate_parameters(case):
    """Deterministic witness geometry, kept completely outside the formula.

    p=2^depth+1 and output extents congruent to 1 mod 2^(stage+1)
    keep both observed mirror paths on real input cells. Increase only the
    concrete witness's source extent until the requested tile fits.
    """
    rank, depth, lanes = case["rank"], case["depth"], case["lanes"]
    stride, delta = 2**depth + 1, 2**depth
    for exponent in range(1, 41):
        extent = 2**exponent
        current = [extent] * rank
        counts = []
        for stage in range(depth):
            parent = [2 * current[0]] + current[1:]
            maximum = [(2 * value - 1) // stride for value in parent]
            modulus = 2**(stage + 1)
            current = [1 + ((value - 1) // modulus) * modulus for value in maximum]
            if min(current) <= 0:
                break
            counts.append(list(current))
        if len(counts) == depth and min(current) > (lanes - 1) * delta:
            break
    else:
        raise ValueError("No bounded certificate recipe found for this profile")
    values = {}
    def vector(label, numbers):
        values.update({f"{label}_{axis}": value for axis, value in enumerate(numbers)})
    vector("extent", [extent] * rank)
    vector("shared_stride", [stride] * rank)
    vector("tile_base", [0] * rank)
    vector("tile_delta", [delta] * rank)
    for stage, sizes in enumerate(counts):
        vector(f"low{stage}", [0] * rank)
        vector(f"high{stage}", [0] * rank)
        vector(f"interior{stage}", [1] * rank)
        vector(f"start{stage}", [0] * rank)
        vector(f"end{stage}", [stride * value for value in sizes])
        values[f"padding_value{stage}"] = 0
    return values


def _row_major(coordinates, extents):
    result = 0
    for coordinate, extent in zip(coordinates, extents):
        result = result * extent + coordinate
    return result


def replay_certificate(case, parameters):
    """Independent Python-only operator and concrete-coordinate evaluation."""
    rank, depth, lanes = case["rank"], case["depth"], case["lanes"]
    values = dict(parameters)
    def vector(label):
        return tuple(values[f"{label}_{axis}"] for axis in range(rank))
    def require(condition, message):
        if not condition:
            raise ValueError("Concrete replay failed: " + message)
    n, step = vector("extent"), vector("shared_stride")
    base, jump = vector("tile_base"), vector("tile_delta")
    require(all(value >= 2 for value in n + step), "input extent/stride profile")
    require(all(value >= 1 for value in jump), "tile displacement profile")
    reads = []
    def source(coordinates):
        value = _row_major(coordinates, n)
        reads.append({"coordinates": list(coordinates), "value": value})
        return value
    lhs = c.Tensor(n, True, source)
    rhs = c.Tensor(n, True, source)
    ml = c.Tensor(n, True, lambda coordinates: True)
    mr = c.Tensor(n, True, lambda coordinates: True)
    for stage in range(depth):
        low, high, interior, start, end = [
            vector(f"{label}{stage}") for label in ("low", "high", "interior", "start", "end")]
        require(all(value >= 1 for value in interior), "positive interior padding")
        padvalue = values[f"padding_value{stage}"]
        def joined(tensor):
            return c.concatenated(tensor, c.reversed_tensor(tensor, [0]))
        pl = c.padded(joined(lhs), low, high, interior, padvalue)
        pr = c.padded(joined(rhs), low, high, interior, padvalue)
        pml = c.padded(joined(ml), low, high, interior, False)
        pmr = c.padded(joined(mr), low, high, interior, False)
        require(all(0 <= s < e <= size for s, e, size in zip(start, end, pl.shape)),
                "nonempty source slice")
        lhs = c.sliced(c.reversed_tensor(pl, range(rank)), start, end, step)
        ml = c.sliced(c.reversed_tensor(pml, range(rank)), start, end, step)
        if stage == depth - 1:
            rstart = tuple(size - e for size, e in zip(pr.shape, end))
            rmstart = tuple(size - e for size, e in zip(pmr.shape, end))
        else:
            rstart = tuple(size - 1 - (s + ((e - s - 1) // p) * p)
                           for size, s, e, p in zip(pr.shape, start, end, step))
            rmstart = tuple(size - 1 - (s + ((e - s - 1) // p) * p)
                            for size, s, e, p in zip(pmr.shape, start, end, step))
        rend = tuple(size - s for size, s in zip(pr.shape, start))
        rmend = tuple(size - s for size, s in zip(pmr.shape, start))
        rhs = c.reversed_tensor(c.sliced(pr, rstart, rend, step), range(rank))
        mr = c.reversed_tensor(c.sliced(pmr, rmstart, rmend, step), range(rank))
    require(lhs.valid and rhs.valid, "both composed operators must be valid")
    require(lhs.shape == rhs.shape, "output shapes must match")
    observations = []
    for lane in range(lanes):
        index = tuple(value + lane * displacement for value, displacement in zip(base, jump))
        require(c.in_bounds(index, lhs.shape) and c.in_bounds(index, rhs.shape), "output access")
        require(ml.access(index) is True and mr.access(index) is True, "source-data-only access")
        observations.append({"index": list(index), "lhs": lhs.access(index), "rhs": rhs.access(index)})
    require(any(row["lhs"] != row["rhs"] for row in observations), "no whole-property violation")
    return {"result": "pass", "level": "independent_python_integer_tensor_chain",
            "equal_valid_shapes": True, "source_data_only": True,
            "lhs_shape": list(lhs.shape), "rhs_shape": list(rhs.shape),
            "observations": observations, "uf_reads": reads}


def verify_certificate(case, text=None, parameters=None):
    """Strict audit + all original assertions + independent operation replay."""
    expected = snapshot_text(case)
    text = expected if text is None else text
    if assertion_fingerprint(text) != assertion_fingerprint(expected):
        raise ValueError("Candidate does not preserve the complete original formula")
    audit = _checker.audit_formula(text)
    if audit["result"] != "pass" or not audit["research_eligible"]:
        raise ValueError("Candidate failed strict QF_UFNIA/research audit")
    values = certificate_parameters(case) if parameters is None else dict(parameters)
    if set(values) != set(case["parameters"]) or any(type(value) is not int for value in values.values()):
        raise ValueError("Certificate must assign every original parameter an exact integer")
    rank = case["rank"]
    extents = [values[f"extent_{axis}"] for axis in range(rank)]
    function = z3.Function("A", *([z3.IntSort()] * (rank + 1)))
    interpretation = sum(
        z3.Var(axis, z3.IntSort()) * math.prod(extents[axis + 1:])
        for axis in range(rank))
    bindings = [(case["parameters"][name], z3.IntVal(value)) for name, value in values.items()]
    assertions = list(z3.parse_smt2_string(text))
    for index, assertion in enumerate(assertions):
        grounded = z3.substitute(assertion, *bindings)
        interpreted = z3.substitute_funs(grounded, (function, interpretation))
        if not z3.is_true(z3.simplify(interpreted)):
            raise ValueError(f"SAT certificate fails original assertion {index}")
    replay = replay_certificate(case, values)
    return {"result": "pass", "instance_id": case["name"],
            "snapshot_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "assertion_fingerprint": assertion_fingerprint(text),
            "assertions_checked": len(assertions), "parameters": values,
            "uf_interpretation": {"symbol": "A", "arity": rank, "extents": extents,
                                  "definition": "row-major offset over exact integer coordinates"},
            "theory": audit, "independent_replay": replay,
            "solver_check_calls": 0,
            "sat_evidence": "Complete explicit total-UF valuation satisfies every original assertion",
            "certificate_constraints_added_to_formula": False,
            "classification": "application_derived",
            "limitation": "Fixed composed integer-tensor model; native Haskell/production HLO not executed"}


def _generate(root, selected, proof_dir):
    summary = {"selected": len(selected), "written": [], "existing": [], "certified_sat": []}
    output = root / "tensorright_xla" / "instance"
    for configuration in selected:
        case = build_case(*configuration)
        text = snapshot_text(case)
        proof = verify_certificate(case, text)
        name = case["name"]
        (proof_dir / (name + ".certificate.json")).write_text(json.dumps(proof, indent=2) + "\n")
        destination = output / (name + ".smt2")
        if destination.exists():
            old = _checker.extract_queries(destination.read_text())
            if len(old) != 1 or assertion_fingerprint(old[0]["text"]) != proof["assertion_fingerprint"]:
                raise ValueError(f"Refusing to replace a different existing formula: {destination}")
            summary["existing"].append(destination.name)
        else:
            header = (
                "; application_derived composed tensor counterexample; explicit SAT valuation verified.\n"
                '(set-info :smt-lib-version 2.6)\n'
                f'(set-info :source "{SOURCE_REPOSITORY}@{SOURCE_COMMIT}; composed typed operators; {name}")\n'
                '(set-info :license "Apache-2.0")\n'
                '(set-info :category "crafted")\n'
                '(set-info :status sat)\n')
            output.mkdir(parents=True, exist_ok=True)
            destination.write_text(header + text)
            summary["written"].append(destination.name)
        summary["certified_sat"].append(name)
    return summary


def generate_harder(root=None, selected=None, proof_dir=None):
    """Publish only certificate-checked SAT snapshots; proof files stay in /tmp."""
    root = CODE.parents[1] if root is None else Path(root).resolve()
    selected = list(DEFAULT_CONFIGS if selected is None else selected)
    if proof_dir is None:
        with tempfile.TemporaryDirectory(prefix="tensor-harder-proof-", dir="/tmp") as temporary:
            return _generate(root, selected, Path(temporary))
    proof_dir = Path(proof_dir).resolve()
    if not proof_dir.is_relative_to(Path("/tmp").resolve()):
        raise ValueError("Explicit proof directories must be under /tmp")
    proof_dir.mkdir(parents=True, exist_ok=True)
    return _generate(root, selected, proof_dir)


def main():
    configurations = {case_name(*config): config for config in DEFAULT_CONFIGS + EXTRA_CONFIGS}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--case", choices=sorted(configurations))
    parser.add_argument("--output-root", type=Path, default=CODE.parents[1])
    parser.add_argument("--proof-dir", type=Path)
    args = parser.parse_args()
    if args.list:
        print("\n".join(configurations))
        return 0
    selected = [configurations[args.case]] if args.case else DEFAULT_CONFIGS
    summary = generate_harder(args.output_root, selected, args.proof_dir)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

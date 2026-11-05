#!/usr/bin/env python3
"""Fixed-rank integer tensor rewrite models; application_derived, never native exports.

Sources and dependencies are pinned in the adjacent source-pin.json. Integer tensor
elements are total UF reads.  Shapes, operator validity, output access and the
NEGATION of the complete selected rewrite obligation are included.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import tempfile
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Callable

import z3

COMMIT = "b3e3a533ff67568db127783cc353ebe04142929f"
REPOSITORY = "https://github.com/ADAPT-uiuc/TensorRight"
SOURCE = "rules/xla/slice/Main.hs"
SOURCE_RULES = {"pad_restore": "rule04", "reverse_slice": "rule13",
                "concat_left": "rule10", "concat_right": "rule11",
                "concat_tail": "rule12", "slice_broadcast": "rule03",
                "nested_slice": "rule07 (new symbolic-stride generalization)"}


@dataclass
class Tensor:
    shape: tuple
    valid: z3.BoolRef
    read: Callable


@dataclass
class Case:
    rule: str
    rank: int
    fault: str | None
    name: str
    assertions: list
    lhs: Tensor
    rhs: Tensor
    index: list
    parameters: dict


def all_of(xs):
    return z3.And(*list(xs))


def in_bounds(index, shape):
    return all_of(z3.And(i >= 0, i < n) for i, n in zip(index, shape))


def tensor(name, sizes):
    shape = tuple(z3.IntVal(n) if isinstance(n, int) else n for n in sizes)
    fun = z3.Function(name, *([z3.IntSort()] * (len(shape) + 1)))
    return Tensor(shape, all_of(n >= 0 for n in shape),
                  lambda ix: fun(*(z3.simplify(i) for i in ix)))


def slice_tensor(t, starts, ends, strides):
    conditions = [t.valid]
    conditions.extend(z3.And(s >= 0, e >= s, e <= n, p > 0)
                      for n, s, e, p in zip(t.shape, starts, ends, strides))
    shape = tuple((e - s + p - 1) / p for s, e, p in zip(starts, ends, strides))
    return Tensor(shape, all_of(conditions),
                  lambda ix: t.read([s + i * p for s, i, p in zip(starts, ix, strides)]))


def pad_tensor(t, low, high, interior, value):
    # This adapter selects the nonnegative exterior-padding subdomain explicitly.
    shape = tuple(n + l + h + z3.If(n > 0, n - 1, 0) * k
                  for n, l, h, k in zip(t.shape, low, high, interior))
    valid = all_of([t.valid] + [z3.And(l >= 0, h >= 0, k >= 0)
                               for l, h, k in zip(low, high, interior)])
    def read(ix):
        relative = [i - l for i, l in zip(ix, low)]
        source_ix = [i / (k + 1) for i, k in zip(relative, interior)]
        occupied = all_of([in_bounds(source_ix, t.shape)] +
                          [i % (k + 1) == 0 for i, k in zip(relative, interior)])
        return z3.If(occupied, t.read(source_ix), value)
    return Tensor(shape, valid, read)


def reverse_tensor(t, axes):
    axes = set(axes)
    return Tensor(t.shape, t.valid,
                  lambda ix: t.read([n - 1 - i if a in axes else i
                                     for a, (i, n) in enumerate(zip(ix, t.shape))]))


def concat_tensor(a, b, axis):
    shape = tuple(x + y if k == axis else x
                  for k, (x, y) in enumerate(zip(a.shape, b.shape)))
    valid = all_of([a.valid, b.valid] +
                   [x == y for k, (x, y) in enumerate(zip(a.shape, b.shape)) if k != axis])
    def read(ix):
        right = list(ix)
        right[axis] = right[axis] - a.shape[axis]
        return z3.If(ix[axis] < a.shape[axis], a.read(ix), b.read(right))
    return Tensor(shape, valid, read)


def broadcast_last(t, extent):
    return Tensor(t.shape + (extent,), z3.And(t.valid, extent >= 0),
                  lambda ix: t.read(ix[:-1]))


def build_case(rule, rank, fault=None):
    if rule not in SOURCE_RULES or rank < 1 or rank > 6:
        raise ValueError("unsupported rule or rank (1..6)")
    if rule == "slice_broadcast" and rank < 2:
        raise ValueError("slice_broadcast needs rank >= 2")
    allowed = {None}
    allowed |= {"unit_stride"} if rule == "pad_restore" else set()
    allowed |= {"naive_mirror"} if rule == "reverse_slice" else set()
    allowed |= {"missing_offset"} if rule == "concat_right" else set()
    allowed |= {"dropped_inner_stride"} if rule == "nested_slice" else set()
    if fault not in allowed:
        raise ValueError("unsupported source-level model variation")
    name = f"{rule}_rank{rank}" + (f"_{fault}" if fault else "")
    parameters = {}
    def vs(label, count=rank):
        xs = [z3.Int(f"{label}_{k}") for k in range(count)]
        parameters.update({str(v): v for v in xs})
        return xs
    n, idx = vs("extent"), vs("index")
    pre = [all_of(v > 0 for v in n)]
    a = tensor("A", n)
    one = [z3.IntVal(1)] * rank

    if rule == "pad_restore":
        low, high, interior = vs("low"), vs("high"), vs("interior")
        p = z3.Int("padding_value")
        parameters[str(p)] = p
        pre += [all_of(v >= 0 for v in low + high + interior)]
        stride = one if fault else [v + 1 for v in interior]
        end = [sz + l + (sz - 1) * k for sz, l, k in zip(n, low, interior)]
        lhs = slice_tensor(pad_tensor(a, low, high, interior, p), low, end, stride)
        rhs = a
    elif rule == "reverse_slice":
        start, end, step = vs("start"), vs("end"), vs("stride")
        pre += [all_of(z3.And(s >= 0, s < e, e <= sz, p > 0)
                       for sz, s, e, p in zip(n, start, end, step))]
        # At least one axis is reversed; the remaining axes are ordinary slices.
        axes = list(range(rank))
        lhs = slice_tensor(reverse_tensor(a, axes), start, end, step)
        rstart = [sz - e if fault else sz - s - (p * ((e - s - 1) / p) + 1)
                  for sz, s, e, p in zip(n, start, end, step)]
        rend = [sz - s for sz, s in zip(n, start)]
        rhs = reverse_tensor(slice_tensor(a, rstart, rend, step), axes)
    elif rule.startswith("concat_"):
        bn = vs("second_extent")
        # Concatenation along axis zero; all other dimensions match.
        bshape = [bn[0]] + n[1:]
        pre += [bn[0] > 0]
        b = tensor("B", bshape)
        start, end, step = vs("start"), vs("end"), vs("stride")
        total = [n[0] + bn[0]] + n[1:]
        if rule == "concat_tail":
            cn = vs("third_extent")
            pre += [cn[0] > 0]
            c = tensor("C", [cn[0]] + n[1:])
            joined = concat_tensor(concat_tensor(a, b, 0), c, 0)
            target = concat_tensor(b, c, 0)
            total[0] = total[0] + cn[0]
        else:
            joined = concat_tensor(a, b, 0)
            target = a if rule == "concat_left" else b
        pre += [all_of(z3.And(s >= 0, s < e, e <= sz, p > 0)
                       for sz, s, e, p in zip(total, start, end, step))]
        pre += [end[0] <= n[0]] if rule == "concat_left" else [start[0] >= n[0]]
        lhs = slice_tensor(joined, start, end, step)
        rstart, rend = list(start), list(end)
        if rule != "concat_left" and not fault:
            rstart[0], rend[0] = start[0] - n[0], end[0] - n[0]
        rhs = slice_tensor(target, rstart, rend, step)
    elif rule == "slice_broadcast":
        base = tensor("A", n[:-1])
        start, end, step = vs("start"), vs("end"), vs("stride")
        pre += [all_of(z3.And(s >= 0, s < e, e <= sz, p > 0)
                       for sz, s, e, p in zip(n, start, end, step))]
        lhs = slice_tensor(broadcast_last(base, n[-1]), start, end, step)
        inner = slice_tensor(base, start[:-1], end[:-1], step[:-1])
        rhs = broadcast_last(inner, (end[-1] - start[-1] + step[-1] - 1) / step[-1])
    else:  # New, explicitly derived symbolic-stride composition of upstream rule07.
        start1, end1, stride1 = vs("inner_start"), vs("inner_end"), vs("inner_stride")
        start2, end2, stride2 = vs("outer_start"), vs("outer_end"), vs("outer_stride")
        inner = slice_tensor(a, start1, end1, stride1)
        pre += [all_of(z3.And(s >= 0, s < e, e <= sz, p > 0)
                       for sz, s, e, p in zip(n, start1, end1, stride1))]
        pre += [all_of(z3.And(s >= 0, s < e, e <= sz, p > 0)
                       for sz, s, e, p in zip(inner.shape, start2, end2, stride2))]
        lhs = slice_tensor(inner, start2, end2, stride2)
        count = lhs.shape
        rstart = [s1 + s2 * p1 for s1, s2, p1 in zip(start1, start2, stride1)]
        rend = [s1 + (s2 + (cnt - 1) * p2) * p1 + 1
                for s1, s2, cnt, p1, p2 in zip(start1, start2, count, stride1, stride2)]
        rstride = stride2 if fault else [p1 * p2 for p1, p2 in zip(stride1, stride2)]
        rhs = slice_tensor(a, rstart, rend, rstride)

    good = z3.And(rhs.valid, in_bounds(idx, rhs.shape),
                  all_of(l == r for l, r in zip(lhs.shape, rhs.shape)),
                  lhs.read(idx) == rhs.read(idx))
    assertions = pre + [lhs.valid, in_bounds(idx, lhs.shape), z3.Not(good)]
    return Case(rule, rank, fault, name, assertions, lhs, rhs, idx, parameters)


def replay_case(case, model):
    # The only solver interaction is reading input constants and input tensor
    # values at concrete integer coordinates. All operation arithmetic, shape,
    # validity, and rewrite evaluation run in the independent Python module.
    import importlib.util
    module_path = Path(__file__).with_name("concrete_replay.py")
    spec = importlib.util.spec_from_file_location("tensorright_concrete_replay", module_path)
    concrete = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(concrete)
    parameters = {
        name: model.eval(symbol, model_completion=True).as_long()
        for name, symbol in case.parameters.items()
    }
    def read_tensor(name, coordinates):
        function = z3.Function(name, *([z3.IntSort()] * (len(coordinates) + 1)))
        application = function(*(z3.IntVal(value) for value in coordinates))
        return model.eval(application, model_completion=True).as_long()
    return concrete.replay(case.rule, case.rank, case.fault, parameters, read_tensor)


def replay(task, model):
    return replay_case(build_case(**task["model_parameters"]), model)


def cases(mode):
    if mode == "pilot":
        return [("pad_restore", 1, None), ("reverse_slice", 1, None),
                ("concat_left", 2, None), ("concat_right", 2, None),
                ("nested_slice", 1, None)]
    out = [(r, n, None) for r in SOURCE_RULES for n in (1, 2, 3)
           if r != "slice_broadcast" or n >= 2]
    for rule, fault in (("pad_restore", "unit_stride"),
                        ("reverse_slice", "naive_mirror"),
                        ("concat_right", "missing_offset"),
                        ("nested_slice", "dropped_inner_stride")):
        out.extend((rule, n, fault) for n in (1, 2))
    return out


def generate(root, selected, argv):
    family = root / "tensorright_xla"
    for sub in ("raw", "code/inputs", "instance", "metadata", "validation"):
        (family / sub).mkdir(parents=True, exist_ok=True)
    for rule, rank, fault in selected:
        case = build_case(rule, rank, fault)
        params = {"rule": rule, "rank": rank, "fault": fault}
        inp = family / "code" / "inputs" / (case.name + ".json")
        inp.write_text(json.dumps(params, sort_keys=True, indent=2) + "\n")
        s = z3.Solver()
        s.add(*case.assertions)
        raw = family / "raw" / (case.name + ".smt2")
        raw.write_text("; application_derived, not a native TensorRight export\n" +
                       "(set-logic QF_UFNIA)\n" + s.to_smt2() + "\n(exit)\n")
        task = {
            "instance_id": case.name, "family": "tensorright_xla",
            "classification": "application_derived",
            "raw_file": str(raw.relative_to(root)),
            "repository": REPOSITORY, "source_commit": COMMIT,
            "source_files": [SOURCE, "src/TensorRight/Internal/Core/Tensor/Typed.hs"],
            "task_id": f"{SOURCE_RULES[rule]}:integer:rank={rank}:variant={fault or 'original'}",
            "property": f"{rule}: operator validity, shape and pointwise integer elements preserved",
            "query_kind": "counterexample",
            "scope_description": "fixed-rank, positive input extents, nonempty slices, unbounded integer tensor elements",
            "bounds": {"rank": rank, "input_extent": "symbolic positive integers",
                       "slice_size": "symbolic positive", "integer_elements": "unbounded"},
            "input_file": str(inp.relative_to(root)),
            "input_hash": hashlib.sha256(inp.read_bytes()).hexdigest(),
            "license": "Apache-2.0; source notice retained in code/LICENSE.TensorRight",
            "uf_meaning": "A/B/C map integer tensor coordinates to unbounded integer elements",
            "integer_nonlinearity_meaning": "symbolic index times stride; pad extents/dilation; symbolic ceil division",
            "source_ids": ["S08", "S09", "S11"],
            "split_group": f"tensorright:{rule}",
            "paired_variant_group": f"tensorright:{rule}:rank{rank}",
            "limitations": [
                "Native Haskell TensorRight verifier not built or run (Stack/GHC absent).",
                "Independent application-derived adapter; no production XLA bug claim.",
                "The rank and nonempty/nonnegative-padding subdomain are explicit restrictions.",
                "Integer elements are not IEEE floating point or fixed-width machine arithmetic."],
            "transformation_relation": "identical",
            "transformations": [],
            "model_parameters": params,
            "replay_module": "tensorright_xla/code/generate.py",
            "generator_command": argv,
            "generator_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "model_source_rule": SOURCE_RULES[rule],
            "model_variation": fault,
        }
        (family / "raw" / (case.name + ".task.json")).write_text(
            json.dumps(task, sort_keys=True, indent=2) + "\n")
        print(case.name)


def _local_checker():
    path = Path(__file__).with_name("common") / "smt_tools.py"
    spec = importlib.util.spec_from_file_location("tensorright_local_smt_tools", path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


def _assertion_fingerprint(text):
    # Parsing expands lets/macros and discards descriptive headers. No solver
    # check is called; unchanged formulas retain their established file bytes.
    assertions = list(z3.parse_smt2_string(text))
    encoded = "\n".join(term.sexpr() for term in assertions).encode()
    return hashlib.sha256(encoded).hexdigest()


def _known_unsat_ids():
    """Recorded experiment exclusions, without solving or classifying new inputs."""
    policy = json.loads(Path(__file__).with_name("known_unsat.json").read_text())
    return {item["instance_id"] for item in policy["known_unsat"]
            if item.get("status") == "unsat"}


def generate_direct(root, selected, argv):
    """Stage old emitter output in /tmp, audit, then publish only full queries."""
    root = Path(root).resolve()
    selected = list(selected)
    checker = _local_checker()
    summary = {"selected": len(selected), "written": [], "existing": [], "excluded": []}
    known_unsat = _known_unsat_ids()
    publishable = []
    for rule, rank, fault in selected:
        identifier = f"{rule}_rank{rank}" + (f"_{fault}" if fault else "")
        if identifier in known_unsat:
            summary["excluded"].append({
                "instance_id": identifier,
                "category": "known_unsat",
                "reasons": ["Known UNSAT ID skipped by the requested experiment policy"],
            })
        else:
            publishable.append((rule, rank, fault))
    output = root / "tensorright_xla" / "instance"
    with tempfile.TemporaryDirectory(prefix="tensorright-export-", dir="/tmp") as temporary:
        staging = Path(temporary)
        with contextlib.redirect_stdout(io.StringIO()):
            generate(staging, publishable, argv)
        for raw in sorted((staging / "tensorright_xla" / "raw").glob("*.smt2")):
            queries = checker.extract_queries(raw.read_text())
            if len(queries) != 1:
                raise ValueError(f"Expected one complete query for {raw.stem}")
            snapshot = queries[0]["text"]
            audit = checker.audit_formula(snapshot)
            if audit["result"] != "pass":
                raise ValueError(f"Strict theory audit failed for {raw.stem}: {audit['errors']}")
            if not audit["research_eligible"]:
                summary["excluded"].append({
                    "instance_id": raw.stem,
                    "reasons": audit["research_eligibility_reasons"],
                })
                continue
            destination = output / raw.name
            if destination.exists():
                old_queries = checker.extract_queries(destination.read_text())
                if (len(old_queries) != 1 or
                        _assertion_fingerprint(old_queries[0]["text"]) != _assertion_fingerprint(snapshot)):
                    raise ValueError(f"Refusing to replace a different existing formula: {destination}")
                summary["existing"].append(raw.name)
                continue
            task = json.loads(raw.with_suffix(".task.json").read_text())
            source = (f"{REPOSITORY}@{COMMIT}; {task['model_source_rule']}; "
                      f"{raw.stem}; application_derived")
            header = (
                "; Fixed-rank integer tensor rewrite counterexample; generated without solving.\n"
                '(set-info :smt-lib-version 2.6)\n'
                f'(set-info :source "{source}")\n'
                '(set-info :license "Apache-2.0")\n'
                '(set-info :category "crafted")\n'
                '(set-info :status unknown)\n'
            )
            output.mkdir(parents=True, exist_ok=True)
            destination.write_text(header + snapshot)
            summary["written"].append(raw.name)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("pilot", "campaign"), default="campaign")
    ap.add_argument("--case", help="one case ID, selected from --list")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[2])
    ns = ap.parse_args()
    selected = cases(ns.mode)
    if ns.case:
        selected = [x for x in selected if build_case(*x).name == ns.case]
        if not selected:
            ap.error("unknown case ID for selected mode")
    if ns.list:
        for x in selected:
            print(build_case(*x).name)
        return
    summary = generate_direct(
        ns.output_root.resolve(), selected,
        [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()

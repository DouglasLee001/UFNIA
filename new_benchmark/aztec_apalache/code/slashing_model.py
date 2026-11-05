"""Bounded reachable SlashingProposer witnesses derived from pinned Quint sources.

This is an application-derived semantic port, not a native Apalache export.
Every trace starts with the source machine's empty maps. Actual storage maps are
UFs; ground read/write instances cover every symbolic key that any operation or
the final goal can observe. Symbolic key aliases share the same UF interpretation.
The resulting finite observations extend to ordinary dictionary states outside
the observed keys. Every emitted assertion is retained in snapshots and certificate
checks. No certificate value is asserted into the query. Generation and certificate
checking never call solver.check().
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import copy
import hashlib
import json
from pathlib import Path
import tarfile
import z3

VERSION = "slashing-source-model-v1"
CODE = Path(__file__).resolve().parent
MAX_UINT256 = 2**256 - 1
MAX_UINT32 = 2**32 - 1
MAX_ROUND_SIZE = 10000
MAX_SUBMIT_ROUND = 1000
ADDRESS_NAMES = (
    "ZERO_ADDRESS", "payload1", "payload2", "payload3", "r1", "sp1", "slasher1",
    "user1", "user2", "user3", "att1", "gov1", "gp1", "gse1", "reg1", "rd1",
    "asset1", "bonus-instance",
)
ADDRESS = {name: i for i, name in enumerate(ADDRESS_NAMES)}
ZERO, INSTANCE, SELF, SLASHER, USER1 = (ADDRESS[n] for n in
    ("ZERO_ADDRESS", "r1", "sp1", "slasher1", "user1"))
PAYLOADS = (1, 2, 3)
PAYLOAD_DOMAIN = (ZERO,) + PAYLOADS
# Exactly ALL_SENDERS from slashing_proposer_machine.qnt:25-39.
SENDERS = tuple(ADDRESS[n] for n in ADDRESS_NAMES if n not in
    {"ZERO_ADDRESS", "asset1", "bonus-instance"})
SOURCE_FILES = (
    "spec/types.qnt", "spec/empire_base.qnt", "spec/slasher.qnt",
    "spec/slashing_proposer.qnt", "spec/slashing_proposer_machine.qnt",
    "spec/invariant.qnt", "spec/slashing_proposer_props.qnt",
)
FUNCTIONS = {
    "signal_count": (4, False), "last_signal_slot": (3, False),
    "winner": (3, False), "executed": (3, True), "round_present": (3, True),
    "payload_present": (4, True), "vetoed": (2, True),
}

@dataclass
class Case:
    profile: dict
    assertions: list
    symbols: dict
    observed_rounds: list
    actions: tuple
    metadata: dict


@lru_cache(maxsize=1)
def _source_provenance():
    locks = json.loads((CODE / "dependencies.json").read_text())
    manifest = json.loads((CODE / "source-manifest.json").read_text())
    archive = CODE / locks["aztec"]["archive"]
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != locks["aztec"]["sha256"]:
        raise ValueError("Pinned Aztec archive hash mismatch")
    verified = {}
    with tarfile.open(archive, "r:gz") as source:
        members = {m.name.split("/", 1)[1]: m for m in source.getmembers() if "/" in m.name}
        for relative in SOURCE_FILES:
            member = members.get(relative)
            if member is None or not member.isfile():
                raise ValueError("Missing pinned source " + relative)
            data = source.extractfile(member).read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != manifest[relative]:
                raise ValueError("Pinned source hash mismatch: " + relative)
            verified[relative] = actual
    return {"source_repository": locks["aztec"]["repository"],
            "source_commit": locks["aztec"]["commit"],
            "source_archive_sha256": digest, "source_file_sha256": verified,
            "source_license": locks["aztec"]["license"]}


def _profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a dict with kind, signals and optional qmin/vetoes")
    unknown = set(profile) - {"kind", "signals", "qmin", "vetoes", "name"}
    if unknown:
        raise ValueError("Unknown profile fields: " + str(sorted(unknown)))
    result = dict(kind=profile.get("kind", "quorum"), signals=profile.get("signals", 2),
                  qmin=profile.get("qmin", 2), vetoes=profile.get("vetoes", 0))
    if result["kind"] not in {"quorum", "executed"}:
        raise ValueError("kind must be quorum or executed")
    for key in ("signals", "qmin", "vetoes"):
        if type(result[key]) is not int:
            raise ValueError(key + " must be an integer")
    if not 2 <= result["signals"] <= 128 or not 2 <= result["qmin"] <= 10000:
        raise ValueError("Require 2<=signals<=128 and 2<=qmin<=10000")
    if result["vetoes"] not in (0, 1) or (result["vetoes"] and result["kind"] != "executed"):
        raise ValueError("A single optional veto is supported before executed witness submit")
    result["name"] = profile.get("name", "slashing_" + result["kind"] + "_signals" +
                                str(result["signals"]) + "_qmin" + str(result["qmin"]) +
                                ("_veto" if result["vetoes"] else ""))
    return result


def build_case(profile):
    profile = _profile(profile)
    symbols = {}
    def integer(name):
        symbols[name] = z3.Int(name)
        return symbols[name]
    def membership(value, domain):
        return z3.Or(*(value == x for x in domain))
    for name, (arity, boolean) in FUNCTIONS.items():
        symbols[name] = z3.Function(name, *([z3.IntSort()] * arity),
                                    z3.BoolSort() if boolean else z3.IntSort())
    count, last, winner, executed, present, payload_present, vetoed = (
        symbols[n] for n in FUNCTIONS)
    R, Q, D, L, timestamp = (integer(n) for n in ("R", "Q", "D", "L", "block_timestamp"))
    actions = (("signal",) * profile["signals"] + ("veto",) * profile["vetoes"] +
               (("submit",) if profile["kind"] == "executed" else ()))
    N = len(actions)
    slots = [integer("slot_" + str(t)) for t in range(N + 1)]
    proposers = [integer("proposer_" + str(t)) for t in range(N + 1)]
    for t, action in enumerate(actions):
        if action in {"signal", "veto"}:
            integer("sender_" + str(t)); integer("payload_" + str(t))
        else:
            integer("submit_round_" + str(t)); integer("callback_caller_" + str(t))
    goal_round = integer("goal_round")
    goal_payload = integer("goal_payload") if profile["kind"] == "quorum" else None
    observed = [slots[t] / R for t, action in enumerate(actions) if action == "signal"]
    observed += [symbols["submit_round_" + str(t)] for t, action in enumerate(actions) if action == "submit"]
    observed.append(goal_round)
    assertions = [
        R > 0, R <= MAX_ROUND_SIZE, R <= MAX_UINT256,
        Q > 1, Q >= profile["qmin"], Q <= MAX_UINT256, 2 * Q > R, Q <= R,
        D >= 0, D <= MAX_UINT256, L > D, L <= MAX_UINT256,
        timestamp >= 0, timestamp <= MAX_UINT32,
    ]
    # Initial and successor rollup maps have exactly singleton domain {r1}.
    # No source action imposes global slot monotonicity or proposer membership.
    for slot in slots:
        assertions.extend([slot >= 0, slot <= MAX_UINT256])
    for r in observed:
        assertions.extend([z3.Not(present(0, INSTANCE, r)), last(0, INSTANCE, r) == 0,
                           winner(0, INSTANCE, r) == ZERO, z3.Not(executed(0, INSTANCE, r))])
        for p in PAYLOAD_DOMAIN:
            assertions.extend([count(0, INSTANCE, r, p) == 0,
                               z3.Not(payload_present(0, INSTANCE, r, p))])
    assertions.extend(z3.Not(vetoed(0, p)) for p in PAYLOAD_DOMAIN)
    for t, action in enumerate(actions):
        write_round = None
        new_winner = None
        if action == "signal":
            sender, payload = symbols[f"sender_{t}"], symbols[f"payload_{t}"]
            write_round = slots[t] / R
            old_winner = winner(t, INSTANCE, write_round)
            old_count = count(t, INSTANCE, write_round, payload)
            old_max = count(t, INSTANCE, write_round, old_winner)
            new_winner = z3.If(z3.And(old_winner != payload, old_count + 1 > old_max),
                               payload, old_winner)
            assertions.extend([membership(sender, SENDERS), membership(payload, PAYLOADS),
                               sender == proposers[t], slots[t] > last(t, INSTANCE, write_round)])
        elif action == "veto":
            sender, payload = symbols[f"sender_{t}"], symbols[f"payload_{t}"]
            assertions.extend([membership(sender, SENDERS), membership(payload, PAYLOADS), sender == USER1])
        else:
            write_round = symbols[f"submit_round_{t}"]
            current_round = slots[t] / R
            old_winner = winner(t, INSTANCE, write_round)
            caller = symbols[f"callback_caller_{t}"]
            assertions.extend([
                write_round >= 0, write_round <= MAX_SUBMIT_ROUND,
                current_round > write_round + D, current_round <= write_round + L,
                z3.Not(executed(t, INSTANCE, write_round)), old_winner != ZERO,
                count(t, INSTANCE, write_round, old_winner) >= Q,
                caller == SELF, z3.Not(vetoed(t, old_winner)),
            ])
        # Complete ground frame closure over every operation/goal round term.
        for r in observed:
            hit = r == write_round if write_round is not None else z3.BoolVal(False)
            old_present = present(t, INSTANCE, r)
            assertions.append(present(t + 1, INSTANCE, r) ==
                              (z3.Or(old_present, hit) if action == "signal" else old_present))
            assertions.append(last(t + 1, INSTANCE, r) ==
                              (z3.If(hit, slots[t], last(t, INSTANCE, r)) if action == "signal" else last(t, INSTANCE, r)))
            assertions.append(winner(t + 1, INSTANCE, r) ==
                              (z3.If(hit, new_winner, winner(t, INSTANCE, r)) if action == "signal" else winner(t, INSTANCE, r)))
            assertions.append(executed(t + 1, INSTANCE, r) ==
                              (z3.Or(executed(t, INSTANCE, r), hit) if action == "submit" else executed(t, INSTANCE, r)))
            for p in PAYLOAD_DOMAIN:
                writes_payload = z3.And(hit, symbols[f"payload_{t}"] == p) if action == "signal" else z3.BoolVal(False)
                assertions.append(count(t + 1, INSTANCE, r, p) ==
                                  (z3.If(writes_payload, count(t, INSTANCE, r, p) + 1,
                                         count(t, INSTANCE, r, p)) if action == "signal" else count(t, INSTANCE, r, p)))
                assertions.append(payload_present(t + 1, INSTANCE, r, p) ==
                                  z3.Or(payload_present(t, INSTANCE, r, p), writes_payload))
        for p in PAYLOAD_DOMAIN:
            assertions.append(vetoed(t + 1, p) ==
                              (z3.Or(vetoed(t, p), symbols[f"payload_{t}"] == p) if action == "veto" else vetoed(t, p)))
    assertions.append(present(N, INSTANCE, goal_round))
    if profile["kind"] == "quorum":
        assertions.extend([membership(goal_payload, PAYLOADS),
                           payload_present(N, INSTANCE, goal_round, goal_payload),
                           count(N, INSTANCE, goal_round, goal_payload) >= Q])
    else:
        assertions.append(executed(N, INSTANCE, goal_round))
    metadata = copy.deepcopy(_source_provenance())
    metadata.update({
        "generator": VERSION, "classification": "application_derived",
        "instance_id": profile["name"], "profile": dict(profile),
        "query_kind": "bounded_reachable_" + profile["kind"] + "_witness",
        "source_property": "signal_quorum_ex" if profile["kind"] == "quorum" else "executed_ex",
        "source_condition": "Negation of the source false invariant over the singleton proposer, after the selected successful action schedule",
        "action_schedule": list(actions), "initialization": "Exact empty map init of slashing_proposer_machine",
        "bounds": {"signal_steps": profile["signals"], "total_steps": N,
                   "round_size_upper": MAX_ROUND_SIZE, "quorum_lower": profile["qmin"],
                   "submit_round_upper": MAX_SUBMIT_ROUND, "payloads": 3, "instances": 1},
        "address_embedding": dict(ADDRESS),
        "address_embedding_reason": "Injective integer representation of finitely many source address literals; observed payload and sender choices are constrained to their exact finite source domains. All address operations in this projection are equality and map indexing.",
        "storage_encoding": "Time-indexed UFs for actual round/payload maps; complete read-over-write frames for all operation/goal symbolic round terms, payload1..3 and ZERO; total extension outside observations is irrelevant",
        "source_ranges": {"signal": "empire_base.qnt:22-87", "submit": "empire_base.qnt:91-147",
                          "callback": "slashing_proposer.qnt:44-63;slasher.qnt:30-48",
                          "empty_init": "slashing_proposer_machine.qnt:104-160",
                          "machine_steps": "slashing_proposer_machine.qnt:162-240",
                          "false_invariants": "slashing_proposer_machine.qnt:280-310",
                          "configuration": "invariant.qnt:887-916", "defaults": "types.qnt:167-173"},
        "limitations": ["Selected bounded successful action schedule; not all source traces",
                        "Semantic Python/Z3 port, not native Apalache solver output",
                        "Protocol-level source model, not production EVM equivalence",
                        "Finite source address universe and exact integer arithmetic without machine overflow",
                        "With K signals, quorum implies Q<=K and majority implies R<2K; effective ranges may be smaller than declared bounds"],
        "existential_projection": "Unused source choices are eliminated: signal _roundNumber in 0..1000, submit _sender/_payload from nonempty domains, and singleton _sp selection. stepTaken is reconstructed by the action schedule and does not occur in either witness property.",
        "unchanged_projection": "Other EVM maps stay empty; slasher PROPOSER=sp1,VETOER=user1; nonces stay empty; block timestamp and immutable configuration are unchanged; callback actions are the source no-actions callback",
    })
    return Case(profile, assertions, symbols, observed, actions, metadata)


def snapshot_text(case):
    declarations = []
    for value in case.symbols.values():
        declaration = value if isinstance(value, z3.FuncDeclRef) else value.decl()
        declarations.append(declaration.sexpr())
    return "\n".join(["(set-logic QF_UFNIA)"] + declarations +
                      ["(assert " + term.sexpr() + ")" for term in case.assertions] +
                      ["(check-sat)", "(exit)", ""])


def _scalars(case, certificate):
    values = certificate.get("scalars", {})
    expected = {name for name, value in case.symbols.items() if not isinstance(value, z3.FuncDeclRef)}
    if set(values) != expected or any(type(value) is not int for value in values.values()):
        raise ValueError("Certificate must assign exactly every scalar symbol using exact integers")
    return values


def _replay(case, values):
    R, Q, D, L = (values[name] for name in ("R", "Q", "D", "L"))
    if not (0 < R <= MAX_ROUND_SIZE and R <= MAX_UINT256 and
            max(2, case.profile["qmin"]) <= Q <= MAX_UINT256 and 2 * Q > R and Q <= R and
            0 <= D < L <= MAX_UINT256 and 0 <= values["block_timestamp"] <= MAX_UINT32):
        raise ValueError("Invalid source initialization configuration")
    for t in range(len(case.actions) + 1):
        if not 0 <= values[f"slot_{t}"] <= MAX_UINT256:
            raise ValueError("rollup slot is outside source Uint256 bounds")
    rounds = {}
    vetoes = set()
    states = [{"rounds": {}, "vetoes": set()}]
    trace = []
    for t, action in enumerate(case.actions):
        slot = values[f"slot_{t}"]
        current_round = slot // R
        if action in {"signal", "veto"}:
            sender, payload = values[f"sender_{t}"], values[f"payload_{t}"]
            if sender not in SENDERS or payload not in PAYLOADS:
                raise ValueError("sender/payload outside exact finite source domain")
        if action == "signal":
            record = rounds.get(current_round, {"last": 0, "winner": ZERO, "executed": False, "counts": {}})
            if slot <= record["last"]:
                raise ValueError("SignalAlreadyCastForSlot at step " + str(t))
            if sender != values[f"proposer_{t}"]:
                raise ValueError("OnlyCurrentProposerCanSignal at step " + str(t))
            record = copy.deepcopy(record)
            incremented = record["counts"].get(payload, 0) + 1
            old_max = record["counts"].get(record["winner"], 0)
            if payload != record["winner"] and incremented > old_max:
                record["winner"] = payload
            record["counts"][payload] = incremented
            record["last"] = slot
            rounds[current_round] = record
            trace.append({"step": t, "action": action, "slot": slot, "round": current_round,
                          "payload": payload, "sender": sender})
        elif action == "veto":
            if sender != USER1:
                raise ValueError("CallerNotVetoer at step " + str(t))
            vetoes.add(payload)
            trace.append({"step": t, "action": action, "payload": payload, "sender": sender})
        else:
            target = values[f"submit_round_{t}"]
            if not 0 <= target <= MAX_SUBMIT_ROUND:
                raise ValueError("submit target outside machine's selected round range")
            if current_round <= target + D:
                raise ValueError("RoundTooNew at step " + str(t))
            if current_round > target + L:
                raise ValueError("RoundTooOld at step " + str(t))
            record = rounds.get(target, {"last": 0, "winner": ZERO, "executed": False, "counts": {}})
            if record["executed"]:
                raise ValueError("PayloadAlreadySubmitted at step " + str(t))
            if record["winner"] == ZERO:
                raise ValueError("PayloadCannotBeAddressZero at step " + str(t))
            if record["counts"].get(record["winner"], 0) < Q:
                raise ValueError("InsufficientSignals at step " + str(t))
            if values[f"callback_caller_{t}"] != SELF:
                raise ValueError("CallerNotProposer at step " + str(t))
            if record["winner"] in vetoes:
                raise ValueError("PayloadVetoed at step " + str(t))
            record = copy.deepcopy(record)
            record["executed"] = True
            rounds[target] = record
            trace.append({"step": t, "action": action, "slot": slot, "round": target,
                          "payload": record["winner"], "callback": "source no-actions"})
        states.append({"rounds": copy.deepcopy(rounds), "vetoes": set(vetoes)})
    target = values["goal_round"]
    record = rounds.get(target)
    if record is None:
        raise ValueError("Final witness round is absent")
    if case.profile["kind"] == "quorum":
        payload = values["goal_payload"]
        if payload not in PAYLOADS or record["counts"].get(payload, 0) < Q:
            raise ValueError("Final quorum witness condition is false")
    elif not record["executed"]:
        raise ValueError("Final execution witness condition is false")
    return states, trace


def replay_certificate(case, certificate):
    """Independent exact-integer dictionary replay; never inspect UF table values."""
    try:
        values = _scalars(case, certificate)
        states, trace = _replay(case, values)
        final = states[-1]
        return {"result": "pass", "level": "independent_python_dictionary_source_transition_replay",
                "steps": trace, "final_rounds": final["rounds"],
                "final_vetoes": sorted(final["vetoes"]), "goal_round": values["goal_round"],
                "source_property": case.metadata["source_property"],
                "native_application_replay": "not_run", "scope": "Selected bounded trace from empty source init"}
    except (ValueError, KeyError, TypeError) as error:
        return {"result": "fail", "reason": str(error),
                "level": "independent_python_dictionary_source_transition_replay"}


def certificate_parameters(case, overrides=None):
    """Construct an explicit total valuation, without adding witness constraints.

    overrides may replace scalar values to exercise other exact source traces.
    It does not modify case.assertions. Unspecified UF arguments use defaults.
    """
    K = case.profile["signals"]
    values = {name: 0 for name, symbol in case.symbols.items() if not isinstance(symbol, z3.FuncDeclRef)}
    values.update(R=2 * K - 1, Q=K, D=1, L=2, block_timestamp=0, goal_round=0)
    if case.profile["kind"] == "quorum":
        values["goal_payload"] = 1
    for t in range(len(case.actions) + 1):
        values[f"proposer_{t}"] = USER1
    for t, action in enumerate(case.actions):
        if action == "signal":
            values[f"slot_{t}"] = t + 1
            values[f"sender_{t}"] = USER1
            values[f"payload_{t}"] = 1
        elif action == "veto":
            values[f"sender_{t}"] = USER1
            values[f"payload_{t}"] = 3
        else:
            values[f"slot_{t}"] = values["R"] * (values["D"] + 1)
            values[f"submit_round_{t}"] = 0
            values[f"callback_caller_{t}"] = SELF
    if overrides:
        if set(overrides) - set(values):
            raise ValueError("Unknown scalar override")
        values.update(overrides)
    certificate = {"format": "slashing-total-valuation-v1", "scalars": values, "functions": {}}
    _scalars(case, certificate)
    states, _ = _replay(case, values)
    tables = {name: {} for name in FUNCTIONS}
    def insert(name, args, value):
        if value != (False if FUNCTIONS[name][1] else 0):
            tables[name][tuple(args)] = value
    for t, state in enumerate(states):
        for r, record in state["rounds"].items():
            insert("round_present", (t, INSTANCE, r), True)
            insert("last_signal_slot", (t, INSTANCE, r), record["last"])
            insert("winner", (t, INSTANCE, r), record["winner"])
            insert("executed", (t, INSTANCE, r), record["executed"])
            for payload, value in record["counts"].items():
                insert("signal_count", (t, INSTANCE, r, payload), value)
                insert("payload_present", (t, INSTANCE, r, payload), True)
        for payload in state["vetoes"]:
            insert("vetoed", (t, payload), True)
    for name, entries in tables.items():
        certificate["functions"][name] = {
            "default": False if FUNCTIONS[name][1] else 0,
            "entries": [{"args": list(args), "value": value} for args, value in sorted(entries.items())],
        }
    return certificate


def verify_certificate(case, certificate):
    """Substitute a complete total UF/int valuation into every original assertion.

    Arithmetic and Boolean expressions are reduced by z3.simplify, without any
    solver call. Every UF application is replaced by its supplied table/default
    value only after its arguments reduce to exact integers. This handles nested
    reads such as count(..., winner(...)) without imposing fresh constraints.
    """
    report = {"result": "fail", "level": "complete_total_uf_valuation_substitution",
              "checked_assertions": 0, "failed_assertion_indices": [], "unresolved_assertion_indices": [],
              "uses_solver_check": False}
    try:
        scalars = _scalars(case, certificate)
        functions = certificate.get("functions", {})
        if set(functions) != set(FUNCTIONS):
            raise ValueError("Certificate must assign exactly every UF")
        tables = {}
        for name, (arity, boolean) in FUNCTIONS.items():
            function = functions[name]
            expected = bool if boolean else int
            if type(function.get("default")) is not expected:
                raise ValueError("Wrong UF default sort: " + name)
            entries = {}
            for entry in function.get("entries", []):
                args, value = entry["args"], entry["value"]
                if len(args) != arity or any(type(a) is not int for a in args) or type(value) is not expected:
                    raise ValueError("Wrong UF entry signature: " + name)
                key = tuple(args)
                if key in entries:
                    raise ValueError("Duplicate UF argument tuple: " + name)
                entries[key] = value
            tables[case.symbols[name].get_id()] = (entries, function["default"])
        pairs = [(case.symbols[name], z3.IntVal(value)) for name, value in scalars.items()]
        cache = {}
        # Z3 may reuse AST IDs after references are released. Retain each
        # grounded node for the complete multi-assertion evaluation lifetime.
        keepalive = {}
        def evaluate(root):
            root = z3.substitute(root, *pairs)
            pending = [(root, False)]
            while pending:
                node, done = pending.pop()
                key = node.get_id()
                if key in cache:
                    continue
                if not done and node.num_args():
                    pending.append((node, True))
                    pending.extend((child, False) for child in node.children() if child.get_id() not in cache)
                    continue
                children = [cache[child.get_id()] for child in node.children()]
                declaration = node.decl()
                if declaration.kind() == z3.Z3_OP_UNINTERPRETED:
                    if declaration.get_id() not in tables or not all(z3.is_int_value(child) for child in children):
                        raise ValueError("Unresolved valuation term: " + node.sexpr())
                    entries, default = tables[declaration.get_id()]
                    value = entries.get(tuple(child.as_long() for child in children), default)
                    reduced = z3.BoolVal(value) if type(value) is bool else z3.IntVal(value)
                else:
                    reduced = z3.simplify(declaration(*children)) if children else node
                keepalive[key] = node
                cache[key] = reduced
            return cache[root.get_id()]
        for index, assertion in enumerate(case.assertions):
            value = evaluate(assertion)
            report["checked_assertions"] += 1
            if not z3.is_true(value):
                report["failed_assertion_indices"].append(index)
                if not z3.is_false(value):
                    report["unresolved_assertion_indices"].append(index)
        report["result"] = "fail" if report["failed_assertion_indices"] else "pass"
        report["valuation_sha256"] = hashlib.sha256(json.dumps(certificate, sort_keys=True,
                                                               separators=(",", ":")).encode()).hexdigest()
    except (ValueError, KeyError, TypeError, z3.Z3Exception) as error:
        report["reason"] = str(error)
    return report

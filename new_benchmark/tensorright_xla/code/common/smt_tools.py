"""SMT-LIB query snapshots and evidence-bounded QF_UFNIA audit.

Snapshots preserve active terms; they never add arithmetic side conditions. Query
indices are zero based. Assertions/assumptions in metadata are serialized terms,
not assert commands. Source command indices, lines, and SHA256 values refer to
the original input. The theory check uses Z3's actual typed, macro-expanded AST,
rather than trusting set-logic. Feature counts are distinct AST terms across
assertions, before/after the documented z3.simplify pass.
"""
from __future__ import annotations
import hashlib
import re
from typing import Union
import z3

SExpr = Union[str, list["SExpr"]]
CHECKER_VERSION = "smt-tools-qf-ufnia-v1"
STANDARD_SOURCES = [
    "https://smt-lib.org/papers/smt-lib-reference-v2.7-r2025-07-07.pdf",
    "https://smt-lib.org/theories-Ints.shtml",
    "https://smt-lib.org/logics-all.shtml#QF_UFNIA",
]

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def dump(expr: SExpr) -> str:
    """Serialize original tokens in linear space without Python recursion."""
    pieces = []
    pending = [expr]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            pieces.append(item)
        elif isinstance(item, list):
            pieces.append("(")
            pending.append(")")
            for index in range(len(item) - 1, -1, -1):
                pending.append(item[index])
                if index:
                    pending.append(" ")
        else:
            raise TypeError("S-expression must consist of lists and original string tokens")
    return "".join(pieces)

def _transform_lists(expr, finish):
    """Explicit postorder list traversal; finish receives transformed children.

    SMT expression depth is unrelated to the Python interpreter's call stack.
    Each frame holds one source list, its next child, and the new child list.
    """
    if not isinstance(expr, list):
        return expr
    pending = [(expr, 0, [])]
    while pending:
        source, index, children = pending[-1]
        if index < len(source):
            child = source[index]
            pending[-1] = (source, index + 1, children)
            if isinstance(child, list):
                pending.append((child, 0, []))
            else:
                children.append(child)
            continue
        pending.pop()
        transformed = finish(children)
        if pending:
            pending[-1][2].append(transformed)
        else:
            return transformed


def _commands_with_locations(text: str) -> list[tuple[list, dict]]:
    stack = []
    results = []
    i = 0
    start = 0
    command_location = {}
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == ";":
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            continue
        if c == "(":
            if not stack:
                start = i
                command_location = {
                    "offset": i,
                    "line": text.count("\n", 0, i) + 1,
                    "column": i - text.rfind("\n", 0, i),
                }
            entry = []
            if stack:
                stack[-1].append(entry)
            stack.append(entry)
            i += 1
            continue
        if c == ")":
            if not stack:
                raise ValueError(f"Unmatched ')' at byte/character offset {i}")
            entry = stack.pop()
            i += 1
            if not stack:
                if not entry or not isinstance(entry[0], str):
                    raise ValueError("Top-level SMT command must have a symbol head")
                location = dict(command_location, end_offset=i)
                results.append((entry, location))
            continue
        token_start = i
        if c == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            else:
                raise ValueError(f"Unterminated SMT string at offset {token_start}")
        elif c == "|":
            i += 1
            while i < n and text[i] != "|":
                if text[i] == "\\":
                    raise ValueError("Backslashes are not permitted in SMT quoted symbols")
                i += 1
            if i == n:
                raise ValueError(f"Unterminated quoted symbol at offset {token_start}")
            i += 1
        else:
            while i < n and not text[i].isspace() and text[i] not in "();":
                if text[i] in '"|':
                    raise ValueError(f"Unexpected token delimiter at offset {i}")
                i += 1
        if not stack:
            raise ValueError(f"Expected parenthesized SMT command at offset {token_start}")
        stack[-1].append(text[token_start:i])
    if stack:
        raise ValueError(f"Unclosed SMT command starting at offset {start}")
    return results

def parse_commands(text: str) -> list[SExpr]:
    """Parse SMT commands, handling comments, quoted symbols and doubled quotes."""
    return [command for command, _ in _commands_with_locations(text)]

_DECLARATIONS = {
    "declare-const", "declare-fun", "declare-sort", "define-fun", "define-sort",
    "declare-datatype", "declare-datatypes",
}
_OBSERVATIONS = {
    "get-model", "get-proof", "get-unsat-core", "get-unsat-assumptions",
    "get-assignment", "get-assertions", "get-value", "get-info", "get-option", "echo",
}
_NONSEMANTIC_OPTIONS = {
    ":print-success", ":produce-models", ":produce-proofs", ":produce-unsat-cores",
    ":produce-unsat-assumptions", ":produce-assignments", ":interactive-mode",
    ":regular-output-channel", ":diagnostic-output-channel", ":verbosity",
    ":random-seed", ":timeout", ":rlimit", ":reproducible-resource-limit",
    ":model", ":proof", ":unsat-core", ":auto-config",
}
_NONSEMANTIC_PREFIXES = (":smt.", ":sat.", ":model.", ":pp.", ":nlsat.", ":arith.")

def _arity(command: list, length: int) -> None:
    if len(command) != length:
        raise ValueError(f"Malformed {command[0]} command: {dump(command)}")

def _symbol(token: SExpr) -> bool:
    return isinstance(token, str) and (
        (token.startswith("|") and token.endswith("|"))
        or bool(re.fullmatch(r"[a-zA-Z~!@$%^&*_\-+=<>.?/][a-zA-Z0-9~!@$%^&*_\-+=<>.?/]*", token))
    )

def _literal(expr: SExpr) -> bool:
    return _symbol(expr) or (
        isinstance(expr, list) and len(expr) == 2 and expr[0] == "not" and _symbol(expr[1])
    )

def _lower_standard(expr, counts=None):
    """Equivalent integer portability rewrites, with explicit term-context frames.

    Only unquoted arithmetic application heads are normalized. Declaration
    signatures, binder pairs, annotation payloads and quoted UF names retain
    their original syntax. Counts describe occurrences actually rewritten.
    """
    prefix = None
    term = expr
    if isinstance(expr, list) and expr and isinstance(expr[0], str):
        if expr[0] in _DECLARATIONS:
            if expr[0] != "define-fun" or len(expr) != 5:
                return expr
            prefix, term = expr[:4], expr[4]
        elif expr[0] == "assert" and len(expr) == 2:
            prefix, term = expr[:1], expr[1]
    def record(method):
        if counts is not None:
            counts[method] = counts.get(method, 0) + 1
    def lower(children):
        if (len(children) == 2 and isinstance(children[0], list) and len(children[0]) == 3
                and children[0][:2] == ["_", "divisible"]):
            numeral = children[0][2]
            if not isinstance(numeral, str) or not re.fullmatch(r"[1-9][0-9]*", numeral):
                raise ValueError("Indexed divisible requires a positive numeral")
            record("indexed_integer_divisible_to_mod")
            return ["=", ["mod", children[1], numeral], "0"]
        if len(children) == 2 and isinstance(children[0], str):
            if children[0] == "+":
                record("integer_unary_add_identity")
                return children[1]
            if children[0] == "*":
                record("integer_unary_multiply_identity")
                return children[1]
        return children
    if not isinstance(term, list):
        transformed = term
    else:
        pending = [(term, 0, [], "term")]
        while pending:
            source, index, children, context = pending[-1]
            if index < len(source):
                child = source[index]
                pending[-1] = (source, index + 1, children, context)
                head = source[0] if source and isinstance(source[0], str) else None
                child_context = "term"
                if context == "plain":
                    child_context = "plain"
                elif context == "bindings":
                    child_context = "binding"
                elif context == "binding" and index == 0:
                    child_context = "plain"
                elif context == "term":
                    if head == "let" and index == 1:
                        child_context = "bindings"
                    elif head in {"forall", "exists", "lambda"} and index == 1:
                        child_context = "plain"
                    elif head == "!" and index != 1:
                        child_context = "plain"
                    elif head in {"as", "_"}:
                        child_context = "plain"
                if isinstance(child, list) and child_context != "plain":
                    pending.append((child, 0, [], child_context))
                else:
                    children.append(child)
                continue
            pending.pop()
            transformed = lower(children) if context == "term" else children
            if pending:
                pending[-1][2].append(transformed)
    return prefix + [transformed] if prefix is not None else transformed

def _named_terms(expr):
    """Postorder named bindings and annotation-free bodies, without recursion."""
    bindings = []
    def strip(children):
        if not children or children[0] != "!":
            return children
        if len(children) < 3:
            raise ValueError("Malformed term annotation")
        term = children[1]
        attrs = children[2:]
        index = 0
        while index < len(attrs):
            attr = attrs[index]
            if attr == ":named":
                if index + 1 >= len(attrs) or not _symbol(attrs[index + 1]):
                    raise ValueError("Named annotation requires a fresh symbol")
                bindings.append((attrs[index + 1], term))
                index += 2
            else:
                index += 1
                if index < len(attrs) and not (isinstance(attrs[index], str) and attrs[index].startswith(":")):
                    index += 1
        # Generated definitions contain the same term, with inline labels
        # stripped so each original label is defined exactly once.
        return term
    stripped = _transform_lists(expr, strip)
    return stripped, bindings


def _active_commands(frames, globals_):
    declarations = sorted(globals_ + [
        item for frame in frames for item in frame["declarations"]
    ], key=lambda item: item[0])
    assertions = sorted([
        item for frame in frames for item in frame["assertions"]
    ], key=lambda item: item[0])
    original_indices = {index for index, _, loc in declarations if "inline_origin" not in loc}
    original_indices.update(index for index, _, _ in assertions)
    records = [(index, command) for index, command, loc in declarations
               if loc.get("inline_origin") not in original_indices]
    records.extend((index, ["assert", term]) for index, term, _ in assertions)
    records.sort(key=lambda item: item[0])
    return records, declarations, assertions

def _assert_fresh(name, frames, globals_, namespace="function"):
    normalized = _symbol_name(name)
    for _, command, _ in globals_ + [item for frame in frames for item in frame["declarations"]]:
        old_namespace = "sort" if command[0] in {"declare-sort", "define-sort"} else "function"
        if old_namespace == namespace and command[1] == name:
            raise ValueError(f"Symbol redeclaration/overload is not permitted: {name}")
        if old_namespace == namespace and isinstance(command[1], str) and _symbol_name(command[1]) == normalized:
            raise ValueError(f"Symbol redeclaration/overload is not permitted: {name}")

def _register_inline_bindings(command, command_index, location, frames, globals_, global_declarations):
    term_args = []
    if command[0] == "assert" and len(command) == 2:
        term_args = [command[1]]
    elif command[0] == "define-fun" and len(command) == 5:
        term_args = [command[4]]
    elif command[0] == "get-value" and len(command) == 2 and isinstance(command[1], list):
        term_args = command[1]
    for term in term_args:
        _, bindings = _named_terms(term)
        for name, body in bindings:
            _assert_fresh(name, frames, globals_)
            records, _, _ = _active_commands(frames, globals_)
            context = "\n".join(dump(c) for _, c in records)
            probe = context + "\n" + dump(["assert", ["=", body, body]])
            nodes = _parse_ast(probe)
            sort = parse_commands("(probe " + nodes[-1].arg(0).sort().sexpr() + ")")[0][1]
            definition = ["define-fun", name, [], sort, body]
            target = globals_ if global_declarations else frames[-1]["declarations"]
            target.append((command_index, definition, dict(location, inline_origin=command_index)))

def _parse_ast(text: str):
    # ALL lets the independent AST whitelist detect hidden theories even when a
    # misleading narrow logic header would make the parser reject earlier.
    commands = parse_commands(text)
    body = "\n".join(dump(_lower_standard(c)) for c in commands if c[0] not in {"set-logic", "check-sat", "exit"})
    try:
        return list(z3.parse_smt2_string("(set-logic ALL)\n" + body))
    except z3.Z3Exception as error:
        raise ValueError("Z3 type/parser check failed: " + str(error)) from error

def extract_queries(text: str) -> list[dict]:
    """Extract independently parseable snapshots with strict stack semantics.

    reset-assertions clears all stack-local declarations and all assertions,
    retaining global declarations and options; reset clears every state. Known
    output/solver-strategy options are recorded as removed. Unrecognized state
    commands/options raise ValueError rather than disappearing silently.
    """
    frames = [{"declarations": [], "assertions": []}]
    globals_ = []
    global_declarations = False
    initialized = False
    logic = None
    exited = False
    segment = 0
    queries = []
    removed = []
    raw_hash = _sha(text)

    for command_index, (command, location) in enumerate(_commands_with_locations(text)):
        head = command[0]
        if exited:
            raise ValueError(f"Command after exit at command {command_index}")
        if head in {"pop", "reset", "reset-assertions"}:
            records, _, _ = _active_commands(frames, globals_)
            _parse_ast("\n".join(dump(c) for _, c in records))
        if head in {"assert", "define-fun", "get-value"}:
            _register_inline_bindings(command, command_index, location, frames, globals_, global_declarations)
        if head == "set-logic":
            _arity(command, 2)
            if initialized or logic is not None:
                raise ValueError("set-logic is only supported once before initialization/reset")
            if not isinstance(command[1], str):
                raise ValueError("set-logic requires a symbol")
            logic = command[1]
        elif head == "set-option":
            _arity(command, 3)
            option = command[1]
            if option in {":global-declarations", ":global-decls"}:
                if initialized:
                    raise ValueError("Changing global-declarations after initialization is unsupported")
                if command[2] not in ("true", "false"):
                    raise ValueError("global-declarations requires true or false")
                global_declarations = command[2] == "true"
            elif isinstance(option, str) and (
                option in _NONSEMANTIC_OPTIONS or option.startswith(_NONSEMANTIC_PREFIXES)
            ):
                removed.append({"command_index": command_index, "command": dump(command),
                                "reason": "output or solver strategy option; no formula change"})
            else:
                raise ValueError(f"Unsupported option semantics: {dump(command)}")
        elif head == "set-info":
            if len(command) not in (2, 3):
                raise ValueError("Malformed set-info")
            removed.append({"command_index": command_index, "command": dump(command),
                            "reason": "descriptive metadata"})
        elif head in _DECLARATIONS:
            expected = {"declare-const": 3, "declare-fun": 4, "declare-sort": 3,
                        "define-fun": 5, "define-sort": 4, "declare-datatype": 3,
                        "declare-datatypes": 3}[head]
            _arity(command, expected)
            if head not in {"declare-datatype", "declare-datatypes"}:
                if not _symbol(command[1]):
                    raise ValueError("Declaration requires a symbol")
                namespace = "sort" if head in {"declare-sort", "define-sort"} else "function"
                _assert_fresh(command[1], frames, globals_, namespace)
            target = globals_ if global_declarations else frames[-1]["declarations"]
            target.append((command_index, command, location))
            initialized = True
        elif head in {"define-fun-rec", "define-funs-rec"}:
            raise ValueError("Recursive definitions are outside QF_UFNIA: " + head)
        elif head == "assert":
            _arity(command, 2)
            frames[-1]["assertions"].append((command_index, command[1], location))
            initialized = True
        elif head in {"push", "pop"}:
            _arity(command, 2)
            if not isinstance(command[1], str) or not re.fullmatch(r"0|[1-9][0-9]*", command[1]):
                raise ValueError(f"{head} requires a non-negative numeral")
            amount = int(command[1])
            if head == "push":
                # A finite representation is necessary; enormous counts cannot
                # be silently truncated.
                if amount > 100000:
                    raise ValueError("push exceeds supported 100000-level resource bound")
                frames.extend({"declarations": [], "assertions": []} for _ in range(amount))
            else:
                if amount >= len(frames):
                    raise ValueError("Unmatched pop exceeds current assertion stack")
                if amount:
                    del frames[-amount:]
            initialized = True
        elif head == "reset-assertions":
            _arity(command, 1)
            frames = [{"declarations": [], "assertions": []}]
            initialized = True
        elif head == "reset":
            _arity(command, 1)
            frames = [{"declarations": [], "assertions": []}]
            globals_ = []
            global_declarations = False
            initialized = False
            logic = None
            segment += 1
            removed = []
        elif head in {"check-sat", "check-sat-assuming"}:
            _arity(command, 1 if head == "check-sat" else 2)
            assumptions = [] if head == "check-sat" else command[1]
            if not isinstance(assumptions, list) or any(not _literal(a) for a in assumptions):
                raise ValueError("check-sat-assuming requires propositional symbol literals")
            records, declarations, assertions = _active_commands(frames, globals_)
            normalization_counts = {}
            active_commands = [(index, dump(_lower_standard(c, normalization_counts)))
                               for index, c in records]
            normalizations = [
                {"method": method, "relation": "equivalent", "count": count}
                for method, count in sorted(normalization_counts.items())
            ]
            assertion_text = [dump(term) for _, term, _ in assertions]
            assumption_text = [dump(term) for term in assumptions]
            snapshot = "\n".join(
                ["(set-logic QF_UFNIA)"] + [s for _, s in active_commands]
                + [dump(["assert", a]) for a in assumptions] + ["(check-sat)", "(exit)", ""]
            )
            _parse_ast(snapshot)
            queries.append({
                "text": snapshot,
                "query_index": len(queries),
                "segment_index": segment,
                "assumptions": assumption_text,
                "active_assertions": assertion_text,
                "original_command": dump(command),
                "source_location": dict(location, command_index=command_index),
                "assertion_sources": [dict(loc, command_index=index) for index, _, loc in assertions],
                "original_logic": logic,
                "global_declarations": global_declarations,
                "inline_definitions": [dump(c) for _, c, loc in declarations if "inline_origin" in loc],
                "normalizations": normalizations,
                "standard_lowerings": [
                    {"indexed_integer_divisible_to_mod": "((_ divisible n) t) becomes (= (mod t n) 0), for positive n",
                     "integer_unary_add_identity": "(+ t) becomes t",
                     "integer_unary_multiply_identity": "(* t) becomes t"}[item["method"]]
                    for item in normalizations
                ],
                "removed_commands": list(removed),
                "transformation": "assuming literals become assertions in this snapshot only",
                "hashes": {
                    "raw_sha256": raw_hash,
                    "snapshot_sha256": _sha(snapshot),
                    "assertions_sha256": [_sha(s) for s in assertion_text],
                    "assumptions_sha256": [_sha(s) for s in assumption_text],
                },
            })
            initialized = True
        elif head in _OBSERVATIONS:
            # These inspect a result but cannot alter any active formula.
            if head in {"get-model", "get-proof", "get-unsat-core", "get-unsat-assumptions",
                        "get-assignment", "get-assertions"}:
                _arity(command, 1)
            elif head in {"get-value", "get-info", "get-option", "echo"}:
                _arity(command, 2)
            removed.append({"command_index": command_index, "command": dump(command),
                            "reason": "observational command"})
        elif head == "exit":
            _arity(command, 1)
            exited = True
        else:
            raise ValueError(f"Unsupported command/state semantics: {dump(command)}")
    # Check even commands after the final query and declarations/assertions
    # never observed at a check-sat, before accepting the source log as valid.
    records, _, _ = _active_commands(frames, globals_)
    _parse_ast("\n".join(dump(c) for _, c in records))
    return queries

_ALLOWED_OP_NAMES = (
    "TRUE", "FALSE", "EQ", "DISTINCT", "ITE", "AND", "OR", "IFF", "XOR",
    "NOT", "IMPLIES", "ANUM", "ADD", "SUB", "UMINUS", "MUL", "IDIV", "MOD",
    "ABS", "LE", "GE", "LT", "GT", "UNINTERPRETED", "DIVIDES",
)
_ALLOWED_OPS = {getattr(z3, "Z3_OP_" + name) for name in _ALLOWED_OP_NAMES
                if hasattr(z3, "Z3_OP_" + name)}
_ALLOWED_SORTS = {z3.Z3_BOOL_SORT, z3.Z3_INT_SORT, z3.Z3_UNINTERPRETED_SORT}

def _walk(roots):
    seen = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        key = node.get_id()
        if key in seen:
            continue
        seen.add(key)
        yield node
        if z3.is_quantifier(node):
            stack.append(node.body())
        elif z3.is_app(node):
            stack.extend(node.children())

def _sort_theory(sort):
    kind = sort.kind()
    names = {
        z3.Z3_REAL_SORT: "Real", z3.Z3_ARRAY_SORT: "Array",
        z3.Z3_BV_SORT: "BitVec", z3.Z3_DATATYPE_SORT: "Datatype",
        z3.Z3_FLOATING_POINT_SORT: "FloatingPoint", z3.Z3_ROUNDING_MODE_SORT: "FloatingPoint",
        z3.Z3_SEQ_SORT: "String/Sequence", z3.Z3_RE_SORT: "RegularExpression",
    }
    return names.get(kind, "UnsupportedSort")

def _inspect(roots):
    sorts = {}
    errors = set()
    extra = set()
    signatures = {}
    for node in _walk(roots):
        if z3.is_quantifier(node):
            extra.add("Quantifier")
            errors.add("Quantifier/lambda is outside quantifier-free first-order QF_UFNIA")
            for index in range(node.num_vars()):
                sort = node.var_sort(index)
                sorts[sort.sexpr()] = sort
            continue
        sort = node.sort()
        sorts[sort.sexpr()] = sort
        if z3.is_app(node):
            decl = node.decl()
            for index in range(decl.arity()):
                arg_sort = decl.domain(index)
                sorts[arg_sort.sexpr()] = arg_sort
            sorts[decl.range().sexpr()] = decl.range()
            kind = decl.kind()
            if kind not in _ALLOWED_OPS:
                errors.add(f"Unsupported operator {decl.name()} (Z3 operator kind {kind})")
                extra.add("Power" if kind == z3.Z3_OP_POWER else "UnsupportedOperator")
            if kind == z3.Z3_OP_UNINTERPRETED:
                signatures[decl.get_id()] = {
                    "symbol": str(decl.name()), "arity": decl.arity(),
                    "domain": [decl.domain(i).sexpr() for i in range(decl.arity())],
                    "range": decl.range().sexpr(),
                }
    for name, sort in list(sorts.items()):
        if sort.kind() not in _ALLOWED_SORTS:
            theory = _sort_theory(sort)
            extra.add(theory)
            errors.add(f"Unsupported sort {name}: {theory}")
    return sorted(sorts), sorted(errors), sorted(extra), list(signatures.values())

def _is_symbolic(node):
    # Constant expressions such as (- 2), (+ 1 2), and (ite true 3 4)
    # are numeric coefficients after this local, non-solving reduction.
    return not z3.is_int_value(z3.simplify(node))

def _nonlinear(node):
    if not z3.is_app(node):
        return False
    kind = node.decl().kind()
    if kind == z3.Z3_OP_MUL and node.sort().kind() == z3.Z3_INT_SORT:
        return sum(_is_symbolic(child) for child in node.children()) >= 2
    return kind in {z3.Z3_OP_IDIV, z3.Z3_OP_MOD} and _is_symbolic(node.arg(1))

def _features(roots):
    stats = {key: 0 for key in (
        "positive_arity_uf_symbols", "positive_arity_uf_applications",
        "symbolic_products", "constant_products", "div", "mod",
        "symbolic_denominator", "uf_nonlinear_arguments", "uf_nonlinear_applications",
        "distinct_uf_argument_terms", "shared_integer_terms", "ast_nodes",
    )}
    symbols = set()
    arguments = set()
    integer_arguments = set()
    outside_integers = set()
    for node in _walk(roots):
        stats["ast_nodes"] += 1
        if not z3.is_app(node):
            continue
        kind = node.decl().kind()
        if kind == z3.Z3_OP_UNINTERPRETED and node.num_args() > 0:
            symbols.add(node.decl().get_id())
            stats["positive_arity_uf_applications"] += 1
            has_nonlinear = False
            for argument in node.children():
                arguments.add(argument.get_id())
                descendants = list(_walk([argument]))
                if any(_nonlinear(descendant) for descendant in descendants):
                    stats["uf_nonlinear_arguments"] += 1
                    has_nonlinear = True
                integer_arguments.update(
                    d.get_id() for d in descendants
                    if d.sort().kind() == z3.Z3_INT_SORT and _is_symbolic(d)
                )
            if has_nonlinear:
                stats["uf_nonlinear_applications"] += 1
        if kind == z3.Z3_OP_MUL and node.sort().kind() == z3.Z3_INT_SORT:
            stats["symbolic_products" if _nonlinear(node) else "constant_products"] += 1
        if kind in {z3.Z3_OP_IDIV, z3.Z3_OP_MOD}:
            stats["div" if kind == z3.Z3_OP_IDIV else "mod"] += 1
            if _is_symbolic(node.arg(1)):
                stats["symbolic_denominator"] += 1
    # Traverse outside UF arguments: x in f(x) and x*x is shared, whereas x
    # appearing solely as the argument to f is not.
    stack = list(roots)
    seen = set()
    while stack:
        node = stack.pop()
        if node.get_id() in seen:
            continue
        seen.add(node.get_id())
        if z3.is_quantifier(node):
            continue
        if node.sort().kind() == z3.Z3_INT_SORT and _is_symbolic(node):
            outside_integers.add(node.get_id())
        if z3.is_app(node):
            if node.decl().kind() == z3.Z3_OP_UNINTERPRETED and node.num_args() > 0:
                continue
            stack.extend(node.children())
    stats["positive_arity_uf_symbols"] = len(symbols)
    stats["distinct_uf_argument_terms"] = len(arguments)
    stats["shared_integer_terms"] = len(integer_arguments & outside_integers)
    return stats

def _symbol_name(symbol):
    return symbol[1:-1] if symbol.startswith("|") and symbol.endswith("|") else symbol

def _definition_checks(commands):
    """Probe every ordinary macro, including unused bodies and argument sorts.

    Probe assertions exist only in checker memory, never in an exported query
    or feature count. The self-equalities retain typed terms before simplify.
    """
    declarations = [c for c in commands if c[0] in _DECLARATIONS]
    base = [c for c in commands if c[0] not in {"set-logic", "check-sat", "exit"}]
    errors, extra, sorts = [], set(), set()
    for declaration in declarations:
        if declaration[0] in {"declare-datatype", "declare-datatypes"}:
            errors.append("Datatype definition is outside QF_UFNIA, including unused datatype definitions")
            extra.add("Datatype")
    prefix = "__smt_audit_probe_"
    all_text = "\n".join(dump(c) for c in commands)
    while prefix in all_text:
        prefix += "_"
    for def_index, command in enumerate(declarations):
        if command[0] not in {"define-fun", "define-sort"}:
            continue
        name = command[1]
        parameters = command[2]
        probes = []
        arguments = []
        if not isinstance(parameters, list):
            errors.append(f"Malformed definition {name}")
            continue
        if command[0] == "define-sort":
            # Instantiate parameterized aliases with allowed fresh Int sorts;
            # aliases to additional theories are rejected even when unused.
            fresh = f"{prefix}{def_index}_sort"
            alias = [name] + ["Int"] * len(parameters) if parameters else name
            probes = [["declare-const", fresh, alias], ["assert", ["=", fresh, fresh]]]
        for index, parameter in enumerate(parameters if command[0] == "define-fun" else []):
            if not isinstance(parameter, list) or len(parameter) != 2:
                errors.append(f"Malformed parameter in definition {name}")
                continue
            fresh = f"{prefix}{def_index}_{index}"
            arguments.append(fresh)
            probes.append(["declare-const", fresh, parameter[1]])
            probes.append(["assert", ["=", fresh, fresh]])
        if command[0] == "define-fun":
            call = [name] + arguments if arguments else name
            probes.append(["assert", ["=", call, call]])
        try:
            ast = _parse_ast("\n".join(dump(c) for c in base + probes))
            found_sorts, found_errors, found_extra, _ = _inspect(ast)
            sorts.update(found_sorts)
            extra.update(found_extra)
            errors.extend(f"Definition {name}: {error}" for error in found_errors)
        except ValueError as error:
            errors.append(f"Definition {name}: {error}")
    return errors, sorted(extra), sorted(sorts)

def audit_formula(text: str) -> dict:
    """Strict membership and research eligibility for one standalone query.

    Membership admits only Core, Ints (excluding power), and uninterpreted
    sorts/functions; unused declarations do not count as actual uses. All
    ordinary definitions are checked, including unused definitions. Research
    eligibility additionally requires positive-arity UF and symbolic nonlinear
    integer work to remain after z3.simplify, without claiming irreducibility.
    """
    result = {
        "result": "fail", "errors": [], "actual_sorts": [], "extra_theories": [],
        "features_before": _features([]), "features_after": _features([]),
        "research_eligible": False, "research_eligibility_reasons": [],
        "checker": CHECKER_VERSION, "checker_version": CHECKER_VERSION,
        "parser": "z3.parse_smt2_string", "z3_version": z3.get_version_string(),
        "preprocessing": "z3.simplify each assertion independently; no solver tactics",
        "feature_counting": "distinct macro-expanded AST nodes across active assertions",
        "function_signatures": [], "unused_declarations": [], "definition_sorts": [],
        "standard_sources": STANDARD_SOURCES,
    }
    try:
        queries = extract_queries(text)
        if len(queries) != 1:
            raise ValueError(f"Audit requires exactly one query, received {len(queries)}")
        snapshot = queries[0]["text"]
        commands = parse_commands(snapshot)
        ast = _parse_ast(snapshot)
        sorts, errors, extra, signatures = _inspect(ast)
        result["actual_sorts"] = sorts
        result["function_signatures"] = sorted(signatures, key=lambda s: (s["symbol"], s["arity"], s["domain"]))
        result["errors"].extend(errors)
        result["extra_theories"] = extra
        result["features_before"] = _features(ast)
        simplified = [z3.simplify(term) for term in ast]
        result["features_after"] = _features(simplified)
        used_symbols = {signature["symbol"] for signature in signatures}
        result["unused_declarations"] = sorted(
            _symbol_name(c[1]) for c in commands
            if c[0] in {"declare-fun", "declare-const"} and _symbol_name(c[1]) not in used_symbols
        )
        def_errors, def_extra, definition_sorts = _definition_checks(commands)
        result["errors"].extend(def_errors)
        result["extra_theories"] = sorted(set(result["extra_theories"]) | set(def_extra))
        result["definition_sorts"] = definition_sorts
        result["result"] = "fail" if result["errors"] else "pass"
        features = result["features_after"]
        if not features["positive_arity_uf_applications"]:
            result["research_eligibility_reasons"].append("No positive-arity UF application after lightweight simplify")
        if not (features["symbolic_products"] or features["symbolic_denominator"]):
            result["research_eligibility_reasons"].append("No symbolic product or symbolic div/mod denominator after lightweight simplify")
        if result["result"] != "pass":
            result["research_eligibility_reasons"].append("Strict theory/type audit failed")
        result["research_eligible"] = not result["research_eligibility_reasons"]
    except (ValueError, z3.Z3Exception, TypeError, IndexError) as error:
        result["errors"].append(str(error))
        result["research_eligibility_reasons"].append("Strict theory/type audit failed")
    return result

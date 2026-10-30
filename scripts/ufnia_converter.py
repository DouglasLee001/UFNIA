"""Z3 preprocessing and serialization for the existing NIA_LS input reader.

No complete SMT solver is invoked here.  The native format is deliberately a
strict subset of the reader's syntax; its numeric parser is only signed int64.
"""

from __future__ import annotations

import re
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import z3

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Decimal input size is part of the integer value, not a machine-width limit.
if hasattr(sys, 'set_int_max_str_digits'):
    sys.set_int_max_str_digits(0)


class ConversionError(Exception):
    """Unsupported input or a conversion resource limit."""


@dataclass
class ConversionResult:
    native: str
    normalized_smt2: str
    metadata: dict
    goal: object = None
    original_assertions: tuple = ()
    native_symbols: object = None
    uf_state: object = None

    def _candidate_model(self, values):
        if getattr(self, '_cached_values', None) is values:
            return self._cached_model
        model = z3.Model(ctx=self.goal.ctx)
        for name, expr in self.native_symbols.items():
            value = values.get(name, False if z3.is_bool(expr) else '0')
            if z3.is_bool(expr):
                parsed = z3.BoolVal(value is True or str(value).lower() == 'true', ctx=expr.ctx)
            else:
                parsed = z3.IntVal(str(value), ctx=expr.ctx)
            model.update_value(expr.decl(), parsed)
        restored = self.goal.convert_model(model)
        self._cached_values, self._cached_model = values, restored
        return restored

    def congruence_conflicts(self, values, max_lemmas=128):
        """Find violated UF congruences in linear space, without pair expansion."""
        if not self.uf_state:
            return []
        model = self._candidate_model(values)
        representatives, conflicts = {}, []
        for decl, arguments, result in self.uf_state['applications']:
            actual_args = tuple(model.eval(arg, model_completion=True) for arg in arguments)
            actual_result = model.eval(result, model_completion=True)
            key = (decl.get_id(), tuple(arg.sexpr() for arg in actual_args))
            previous = representatives.get(key)
            if previous is None:
                representatives[key] = (arguments, result, actual_result)
            elif not z3.eq(previous[2], actual_result):
                guard = z3.And(*(a == b for a, b in zip(previous[0], arguments)))
                conflicts.append(z3.Implies(guard, previous[1] == result))
                if len(conflicts) >= max_lemmas:
                    break
        return conflicts

    def restore_model(self, values):
        """Undo preprocessing and install a consistent finite UF interpretation."""
        restored = self._candidate_model(values)
        if self.congruence_conflicts(values):
            return restored, False
        if self.uf_state:
            functions = {}
            for decl, arguments, result in self.uf_state['applications']:
                actual_args = tuple(restored.eval(arg, model_completion=True) for arg in arguments)
                actual_result = restored.eval(result, model_completion=True)
                if decl.get_id() not in functions:
                    default = z3.BoolVal(False, ctx=decl.ctx) if decl.range().kind() == z3.Z3_BOOL_SORT else z3.IntVal(0, ctx=decl.ctx)
                    interpretation = z3.Z3_add_func_interp(decl.ctx_ref(), restored.model, decl.ast, default.as_ast())
                    # Keep the C API interpretation alive while subsequent
                    # model evaluations allocate ASTs and trigger collection.
                    functions[decl.get_id()] = z3.FuncInterp(interpretation, decl.ctx)
                vector = z3.AstVector(ctx=decl.ctx)
                for value in actual_args:
                    vector.push(value)
                z3.Z3_func_interp_add_entry(decl.ctx_ref(), functions[decl.get_id()].f, vector.vector, actual_result.as_ast())
        valid = all(z3.is_true(restored.eval(assertion, model_completion=True)) for assertion in self.original_assertions)
        return restored, valid

    def refine(self, accumulated_lemmas, timeout_ms=None):
        if not self.uf_state:
            raise ConversionError('no UF abstraction to refine')
        return convert_assertions(self.uf_state['base'] + tuple(accumulated_lemmas),
                                  self.uf_state['timeout_ms'] if timeout_ms is None else max(1, int(timeout_ms)), self.uf_state['ackermann_limit'],
                                  arbitrary_precision=True, _uf_state=self.uf_state)


_TOKENS = re.compile(r';[^\r\n]*|"(?:[^"]|"")*"|\|[^|]*\||[()]|[^\s();]+')
_COMMANDS = {
    'set-logic', 'set-info', 'set-option', 'declare-fun', 'declare-const',
    'declare-sort', 'define-sort', 'define-fun', 'assert', 'check-sat', 'get-model', 'get-info', 'get-option',
    'get-assertions', 'exit',
}


def _check_commands(text: str) -> None:
    depth, head, checked, exited = 0, False, False, False
    command = None
    for match in _TOKENS.finditer(text):
        token = match.group()
        if token.startswith(';'):
            continue
        if token == '(':
            if command == 'check-sat':
                raise ConversionError('check-sat arguments/assumptions are unsupported')
            if depth == 0:
                head = True
            depth += 1
        elif token == ')':
            depth -= 1
            if depth < 0:
                raise ConversionError('malformed SMT-LIB: unmatched closing parenthesis')
            if depth == 0:
                command = None
        elif head:
            head = False
            command = token
            if exited:
                raise ConversionError('commands after exit are unsupported')
            if token not in _COMMANDS:
                raise ConversionError(f'unsupported command {token!r}; require one non-incremental check-sat context')
            if token == 'check-sat':
                if checked:
                    raise ConversionError('multiple check-sat commands are unsupported')
                checked = True
            elif checked and token in {'assert', 'declare-fun', 'declare-const', 'define-fun'}:
                raise ConversionError('formula changes after check-sat are unsupported')
            if token == 'exit':
                exited = True
        elif depth == 0:
            raise ConversionError('malformed SMT-LIB: expected a top-level command')
        elif command == 'check-sat':
            raise ConversionError('check-sat arguments/assumptions are unsupported')
    if depth or head:
        raise ConversionError('malformed SMT-LIB: unclosed command')
    if not checked:
        raise ConversionError('input must contain exactly one check-sat command')


def _nodes(expressions, max_nodes=1_000_000):
    pending, seen = list(expressions), set()
    while pending:
        expr = pending.pop()
        if expr.get_id() in seen:
            continue
        seen.add(expr.get_id())
        if max_nodes is not None and len(seen) > max_nodes:
            raise ConversionError('AST resource limit exceeded (1000000 nodes)')
        yield expr
        pending.extend(expr.children())


def _check_input(expressions, max_nodes=1_000_000, allow_carriers=False):
    allowed = {
        z3.Z3_OP_TRUE, z3.Z3_OP_FALSE, z3.Z3_OP_UNINTERPRETED,
        z3.Z3_OP_ANUM, z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
        z3.Z3_OP_ITE, z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT,
        z3.Z3_OP_IFF, z3.Z3_OP_XOR, z3.Z3_OP_IMPLIES,
        z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS, z3.Z3_OP_MUL,
        z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT,
        z3.Z3_OP_IDIV, z3.Z3_OP_MOD, z3.Z3_OP_REM,
    }
    functions, carriers = [], False
    for expr in _nodes(expressions, max_nodes=max_nodes):
        if z3.is_quantifier(expr) or not z3.is_app(expr):
            raise ConversionError('quantifiers and bound variables are unsupported')
        sort = expr.sort().kind()
        if allow_carriers and sort == z3.Z3_UNINTERPRETED_SORT:
            carriers = True
        elif sort not in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT):
            raise ConversionError(f'unsupported sort {expr.sort()}; only Int and Bool are supported')
        kind = expr.decl().kind()
        if kind not in allowed:
            raise ConversionError(f'unsupported operator {expr.decl().name()}')
        if kind == z3.Z3_OP_UNINTERPRETED and expr.num_args():
            functions.append(expr)
    return functions, carriers


def _numeral(value: int, native: bool) -> str:
    return str(value) if native or value >= 0 else f'( - {-value} )'


class _NativeWriter:
    def __init__(self, arbitrary_precision=False):
        self.arbitrary_precision = arbitrary_precision
        self.symbols = {}
        self.symbol_map = {}
        self.native_symbols = {}
        self.polynomials = {}
        self.atoms = []
        self.atom_ids = {}
        self.clauses = []
        self.definitions = []
        self.product_count = 0

    def symbol(self, expr) -> str:
        key = expr.get_id()
        if key not in self.symbols:
            name = ('b_' if z3.is_bool(expr) else 'i_') + str(len(self.symbol_map))
            self.symbols[key] = name
            self.symbol_map[name] = {'source': str(expr.decl().name()), 'sort': str(expr.sort())}
            self.native_symbols[name] = expr
        return self.symbols[key]

    def fresh_integer(self, purpose: str) -> str:
        name = f'i_{len(self.symbol_map)}'
        self.symbol_map[name] = {'source': None, 'sort': 'Int', 'purpose': purpose}
        return name

    @staticmethod
    def add(left, right, sign=1):
        result = left.copy()
        for term, coeff in right.items():
            value = result.get(term, 0) + sign * coeff
            if value:
                result[term] = value
            else:
                result.pop(term, None)
        if len(result) > 100_000:
            raise ConversionError('polynomial resource limit exceeded (100000 terms)')
        return result

    def materialize(self, poly):
        if len(poly) == 1:
            (term, coeff), = poly.items()
            if len(term) == 1 and coeff == 1:
                return term[0]
        name = self.fresh_integer('binary product operand')
        self.definitions.append(self.add({(name,): 1}, poly, -1))
        return name

    def multiply(self, left, right):
        if not left or not right:
            return {}
        if set(left) == {()}:
            return {t: c * left[()] for t, c in right.items()}
        if set(right) == {()}:
            return {t: c * right[()] for t, c in left.items()}
        # Lift factors instead of expanding large sums or emitting degree > 2.
        a, b = self.materialize(left), self.materialize(right)
        name = self.fresh_integer('binary product')
        self.definitions.append({(name,): 1, tuple(sorted((a, b))): -1})
        self.product_count += 1
        return {(name,): 1}

    def polynomial(self, expr):
        pending = [(expr, False)]
        while pending:
            item, ready = pending.pop()
            key = item.get_id()
            if key in self.polynomials:
                continue
            if not ready:
                if z3.is_int_value(item):
                    self.polynomials[key] = {(): item.as_long()} if item.as_long() else {}
                    continue
                if z3.is_const(item) and item.decl().kind() == z3.Z3_OP_UNINTERPRETED and z3.is_int(item):
                    self.polynomials[key] = {(self.symbol(item),): 1}
                    continue
                if item.decl().kind() not in {z3.Z3_OP_ADD, z3.Z3_OP_SUB, z3.Z3_OP_UMINUS, z3.Z3_OP_MUL}:
                    raise ConversionError(f'unsupported residual arithmetic operator {item.decl().name()}; UF/div/mod/ITE elimination was incomplete')
                pending.append((item, True))
                pending.extend((child, False) for child in reversed(item.children()))
                continue
            children = [self.polynomials[c.get_id()] for c in item.children()]
            kind = item.decl().kind()
            if kind == z3.Z3_OP_MUL:
                value = {(): 1}
                for child in children:
                    value = self.multiply(value, child)
            elif kind == z3.Z3_OP_UMINUS:
                value = {t: -c for t, c in children[0].items()}
            else:
                value = {}
                for index, child in enumerate(children):
                    value = self.add(value, child, -1 if kind == z3.Z3_OP_SUB and index else 1)
            self.polynomials[key] = value
        return self.polynomials[expr.get_id()]

    def check_integer(self, value):
        if self.arbitrary_precision:
            return
        if not -(1 << 63) <= value < (1 << 63):
            raise ConversionError(f'constant/coefficient {value} is outside signed int64 range; NIA_LS uses atoll (arbitrary-precision input is unsupported)')

    def atom(self, relation, poly):
        rhs = -poly.get((), 0)
        terms = tuple(sorted((t, c) for t, c in poly.items() if t and c))
        if not terms:
            return (0 == rhs) if relation == '=' else (0 <= rhs)
        self.check_integer(rhs)
        if not self.arbitrary_precision and rhs == -(1 << 63):
            raise ConversionError('RHS -9223372036854775808 is unsupported: the NIA_LS reader negates it in int64 and overflows')
        for _, coeff in terms:
            self.check_integer(coeff)
        key = (relation, terms, rhs)
        return self.store_atom(key)

    def store_atom(self, key):
        if key not in self.atom_ids:
            self.atoms.append(key)
            self.atom_ids[key] = len(self.atoms)
        return self.atom_ids[key]

    def literal(self, expr):
        negative = False
        while z3.is_not(expr):
            expr = expr.arg(0)
            negative = not negative
        if z3.is_true(expr) or z3.is_false(expr):
            value = z3.is_true(expr)
        elif z3.is_const(expr) and z3.is_bool(expr) and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            value = self.store_atom(('bool', self.symbol(expr)))
        else:
            kind = expr.decl().kind()
            if kind not in {z3.Z3_OP_EQ, z3.Z3_OP_LE, z3.Z3_OP_GE, z3.Z3_OP_LT, z3.Z3_OP_GT} or expr.num_args() != 2 or not all(z3.is_int(a) for a in expr.children()):
                raise ConversionError(f'unsupported residual Boolean atom {expr.decl().name()}')
            left, right = (self.polynomial(expr.arg(i)) for i in range(2))
            if kind in (z3.Z3_OP_GE, z3.Z3_OP_GT):
                left, right = right, left
            poly = self.add(left, right, -1)
            if kind in (z3.Z3_OP_LT, z3.Z3_OP_GT):
                poly = self.add(poly, {(): 1})
            value = self.atom('=' if kind == z3.Z3_OP_EQ else '<=', poly)
        if type(value) is bool:
            return value != negative
        return -value if negative else value

    def encode(self, expressions):
        for expr in expressions:
            disjuncts = expr.children() if z3.is_or(expr) else [expr]
            refs, true_clause = [], False
            for child in disjuncts:
                lit = self.literal(child)
                if lit is True:
                    true_clause = True
                    break
                if lit is not False:
                    if -lit in refs:
                        true_clause = True
                        break
                    if lit not in refs:
                        refs.append(lit)
            if true_clause:
                continue
            if not refs:
                self.__init__(self.arbitrary_precision)
                name = self.fresh_integer('constant false witness')
                self.clauses = [[self.atom('<=', {(name,): 1})], [self.atom('<=', {(name,): -1, (): 1})]]
                return
            self.clauses.append(refs)
        for poly in self.definitions:
            atom = self.atom('=', poly)
            if atom is False:
                raise ConversionError('inconsistent internal product definition')
            if atom is not True:
                self.clauses.append([atom])

    @staticmethod
    def body(atom, native):
        if atom[0] == 'bool':
            return atom[1]
        relation, terms, rhs = atom
        parts = []
        for variables, coeff in terms:
            term = variables[0] if len(variables) == 1 else '( * ' + ' '.join(variables) + ' )'
            if coeff != 1:
                term = f'( * {_numeral(coeff, native)} {term} )'
            parts.append(term)
        lhs = parts[0] if len(parts) == 1 else '( + ' + ' '.join(parts) + ' )'
        return f'( {relation} {lhs} {_numeral(rhs, native)} )'

    def native_text(self):
        lines = [str(len(self.atoms) + 1)]
        lines += [f'{i} {self.body(atom, True)}' for i, atom in enumerate(self.atoms, 1)]
        lines += ['0', str(len(self.clauses))]
        lines += ['( ' + ' '.join(map(str, clause)) + ' )' for clause in self.clauses]
        return '\n'.join(lines + ['0', ''])

    def smt2_text(self):
        lines = ['(set-logic QF_NIA)']
        lines += [f'(declare-fun {name} () {info["sort"]})' for name, info in self.symbol_map.items()]
        for clause in self.clauses:
            parts = [self.body(self.atoms[abs(ref) - 1], False) for ref in clause]
            parts = [f'(not {part})' if ref < 0 else part for ref, part in zip(clause, parts)]
            lines.append('(assert (or ' + ' '.join(parts) + '))')
        return '\n'.join(lines + ['(check-sat)', ''])


def _encode_carrier_sorts(assertions, known_carriers=False):
    """Embed equality-only uninterpreted carriers in Int, preserving UF sorts.

    Only the finitely many ground values are relevant. Each original carrier
    has its own declarations, so encoding carriers into Int introduces no
    comparisons between values from different source sorts.
    """
    has_carriers = known_carriers or any(expr.sort().kind() == z3.Z3_UNINTERPRETED_SORT for expr in _nodes(assertions, max_nodes=None))
    if not has_carriers:
        return tuple(assertions), False
    rewritten, declarations = {}, {}
    ctx = assertions[0].ctx
    def sort(s):
        return z3.IntSort(ctx) if s.kind() == z3.Z3_UNINTERPRETED_SORT else s
    pending = [(expr, False) for expr in assertions]
    while pending:
        expr, ready = pending.pop()
        key = expr.get_id()
        if key in rewritten:
            continue
        if z3.is_quantifier(expr) or not z3.is_app(expr):
            raise ConversionError('quantifiers are unsupported')
        if expr.sort().kind() not in (z3.Z3_INT_SORT, z3.Z3_BOOL_SORT, z3.Z3_UNINTERPRETED_SORT):
            raise ConversionError(f'unsupported sort {expr.sort()}')
        if not ready:
            pending.append((expr, True))
            pending.extend((child, False) for child in expr.children())
            continue
        children = [rewritten[child.get_id()] for child in expr.children()]
        kind = expr.decl().kind()
        if kind == z3.Z3_OP_UNINTERPRETED:
            decl = expr.decl()
            changed = decl.range().kind() == z3.Z3_UNINTERPRETED_SORT or any(decl.domain(i).kind() == z3.Z3_UNINTERPRETED_SORT for i in range(decl.arity()))
            if changed:
                if decl.get_id() not in declarations:
                    signatures = [sort(decl.domain(i)) for i in range(decl.arity())] + [sort(decl.range())]
                    declarations[decl.get_id()] = z3.FreshFunction(*signatures) if decl.arity() else z3.FreshConst(signatures[0]).decl()
                decl = declarations[decl.get_id()]
            rewritten[key] = decl(*children)
        elif kind == z3.Z3_OP_EQ:
            rewritten[key] = children[0] == children[1]
        elif kind == z3.Z3_OP_DISTINCT:
            rewritten[key] = z3.Distinct(*children)
        elif kind == z3.Z3_OP_ITE:
            rewritten[key] = z3.If(*children)
        else:
            rewritten[key] = expr.decl()(*children)
    return tuple(rewritten[expr.get_id()] for expr in assertions), True


def _abstract_functions(assertions):
    rewritten, changed, applications = {}, {}, []
    pending = [(expr, False) for expr in assertions]
    while pending:
        expr, ready = pending.pop()
        key = expr.get_id()
        if key in rewritten:
            continue
        children = expr.children()
        if not ready:
            pending.append((expr, True))
            pending.extend((child, False) for child in children)
            continue
        args = tuple(rewritten[child.get_id()] for child in children)
        if expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and args:
            result = z3.FreshConst(expr.sort(), prefix='ufnia_app')
            rewritten[key], changed[key] = result, True
            applications.append((expr.decl(), args, result))
        elif any(changed[child.get_id()] for child in children):
            rewritten[key], changed[key] = expr.decl()(*args), True
        else:
            rewritten[key], changed[key] = expr, False
    return tuple(rewritten[expr.get_id()] for expr in assertions), tuple(applications)


def _uf_metadata(state):
    if not state:
        return {}
    declarations = {}
    applications = []
    for decl, args, result in state['applications']:
        key = str(decl.get_id())
        if key not in declarations:
            declarations[key] = {'name': str(decl.name()), 'domain': [str(decl.domain(i)) for i in range(decl.arity())], 'range': str(decl.range())}
        applications.append({'function': key, 'arguments': [arg.sexpr() for arg in args], 'value': result.sexpr()})
    return {'uf_mode': state['mode'], 'uf_pair_upper_bound': state['pair_bound'],
            'uf_declarations': declarations, 'uf_applications': applications,
            'requires_congruence_refinement': state['mode'] == 'lazy',
            'intermediate_semantics': 'normalized SMT-LIB plus this UF application table; SAT requires congruence checking' if state['mode'] == 'lazy' else 'equisatisfiable normalized SMT-LIB'}


def convert_assertions(assertions, timeout_ms=30_000, ackermann_limit=100_000,
                       arbitrary_precision=False, _uf_state=None) -> ConversionResult:
    start = time.monotonic()
    def trace(stage):
        if os.environ.get('UFNIA_TRACE'):
            print(f'ufnia_frontend {stage} {time.monotonic()-start:.3f}s', file=sys.stderr, flush=True)
    trace('start')
    if os.environ.get('UFNIA_TRACE'):
        z3.set_param(verbose=10)
    assertions = tuple(assertions)
    ctx = assertions[0].ctx if assertions else z3.Context()
    try:
        original_assertions, carrier_encoding = assertions, False
        state = _uf_state
        if state is not None:
            original_assertions, carrier_encoding = state['original'], state['carrier_encoding']
        elif arbitrary_precision:
            from UFNIA_LS.native_frontend import prepare, UnsupportedCarrier, NativeFrontendError
            try:
                prepared, applications = prepare(assertions)
            except UnsupportedCarrier:
                _, carrier_encoding = _check_input(assertions, max_nodes=None, allow_carriers=True)
                assertions, _ = _encode_carrier_sorts(assertions, known_carriers=True)
                prepared, applications = prepare(assertions)
            except NativeFrontendError as error:
                raise ConversionError(str(error)) from error
            trace('input_checked_and_uf_abstracted')
            original_assertions = assertions
            assertions = prepared
            if applications:
                counts = Counter(decl.get_id() for decl, _, _ in applications)
                pair_bound = sum(n * (n - 1) // 2 for n in counts.values())
                eager = pair_bound <= min(ackermann_limit, 10_000)
                lemmas = []
                if eager:
                    grouped = {}
                    for decl, args, value in applications:
                        for previous_args, previous_value in grouped.get(decl.get_id(), []):
                            lemmas.append(z3.Implies(z3.And(*(a == b for a, b in zip(args, previous_args))), value == previous_value))
                        grouped.setdefault(decl.get_id(), []).append((args, value))
                assertions += tuple(lemmas)
                state = {'applications': applications, 'base': assertions, 'original': original_assertions,
                         'carrier_encoding': carrier_encoding, 'pair_bound': pair_bound,
                         'mode': 'eager' if eager else 'lazy', 'timeout_ms': timeout_ms,
                         'ackermann_limit': ackermann_limit}
        else:
            _check_input(assertions)
        goal = z3.Goal(ctx=ctx)
        goal.add(assertions)
        trace('goal_built')
        tactic = lambda name: z3.Tactic(name, ctx=ctx)
        def optional(name):
            # These are optimizations, not required lowering passes. Large
            # ground formulas must remain readable even if elimination is slow.
            step = tactic(name)
            return z3.OrElse(z3.TryFor(step, 1000), tactic('skip')) if arbitrary_precision else step
        steps = [
            z3.With(tactic('simplify'), blast_distinct=True),
            optional('propagate-values'), optional('solve-eqs'), tactic('elim-term-ite'),
            # In the quantifier-free Int fragment, complete=False retains the
            # congruence axioms for div/mod, including division by zero.
            z3.With(tactic('purify-arith'), complete=False),
            tactic('elim-term-ite'),
        ]
        if not arbitrary_precision:
            steps.append(z3.With(tactic('ackermannize_bv'), div0_ackermann_limit=ackermann_limit))
        steps += [
            z3.With(tactic('simplify'), arith_lhs=True, blast_distinct=True),
            tactic('tseitin-cnf'),
        ]
        chain = z3.Then(*steps, ctx=ctx)
        result = z3.TryFor(chain, max(1, int(timeout_ms)))(goal)
        trace('tactics_complete')
        if len(result) != 1 or result[0].precision() != z3.Z3_GOAL_PRECISE:
            raise ConversionError('preprocessor did not produce one precise goal')
        if arbitrary_precision:
            # The GMP evaluator accepts general polynomial ASTs. Do not run
            # the reference reader's Python atom formatter over huge DAGs.
            from UFNIA_LS.native_frontend import analyze_normalized, serialize_normalized
            try:
                symbols, atom_count, normalized_nodes = analyze_normalized(result[0])
                trace('residual_checked')
                normalized = serialize_normalized(result[0], symbols)
            except NativeFrontendError as error:
                raise ConversionError(str(error)) from error
            native = normalized
            native_symbols = {('b_' if z3.is_bool(expr) else 'i_') + str(i): expr for i, expr in enumerate(symbols)}
            symbol_map = {name: {'source': str(expr.decl().name()), 'sort': str(expr.sort())} for name, expr in native_symbols.items()}
            clause_count, product_count = len(result[0]), 0
        else:
            for expr in _nodes(result[0]):
                if z3.is_quantifier(expr) or not z3.is_app(expr):
                    raise ConversionError('unsupported residual quantified expression')
                if expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and expr.num_args():
                    raise ConversionError(f'UF elimination incomplete (Ackermann resource limit {ackermann_limit}); residual function {expr.decl().name()}')
                if expr.decl().kind() in (z3.Z3_OP_IDIV, z3.Z3_OP_MOD, z3.Z3_OP_REM):
                    raise ConversionError('integer div/mod elimination incomplete')
            writer = _NativeWriter()
            trace('residual_checked')
            writer.encode(result[0])
            trace('native_encoded')
            native, normalized = writer.native_text(), writer.smt2_text()
            native_symbols, symbol_map = writer.native_symbols, writer.symbol_map
            atom_count, clause_count, product_count = len(writer.atoms), len(writer.clauses), writer.product_count
            normalized_nodes = None
        trace('text_serialized')
        metadata = {
            'format': 'QF_NIA SMT-LIB' if arbitrary_precision else 'NIA_LS atom-table/CNF',
            'arbitrary_precision': arbitrary_precision, 'carrier_sort_encoding': carrier_encoding,
            'frontend_engine': 'native_z3_dag' if arbitrary_precision else 'python_legacy',
            'z3_version': z3.get_version_string(),
            'z3_full_version': z3.get_full_version(),
            'atom_count': atom_count, 'clause_count': clause_count,
            'input_assertion_count': len(assertions),
            'integer_variable_count': sum(v['sort'] == 'Int' for v in symbol_map.values()),
            'boolean_variable_count': sum(v['sort'] == 'Bool' for v in symbol_map.values()),
            'binary_product_count': product_count, 'normalized_ast_nodes': normalized_nodes,
            'symbol_map': symbol_map, 'ackermann_limit': ackermann_limit,
            'conversion_seconds': round(time.monotonic() - start, 6),
            'model_reconstruction': 'Z3 goal model converter; opaque carrier representatives remain encoded' if arbitrary_precision else 'not provided; preprocessing and SLS can eliminate original symbols',
            'recommended_solver_options': ['--seconds', '600'] if arbitrary_precision else ['--strategy=corewarm2', '--initialize=off', '--restart-repair=off'],
        }
        metadata.update(_uf_metadata(state))
        trace('metadata_complete')
        return ConversionResult(native, normalized, metadata, result[0], original_assertions, native_symbols, state)
    except z3.Z3Exception as error:
        raise ConversionError(f'Z3 parse/preprocessing failed: {error}') from error


def convert_text(text: str, timeout_ms=30_000, ackermann_limit=100_000,
                 arbitrary_precision=False) -> ConversionResult:
    _check_commands(text)
    ctx = z3.Context()
    try:
        assertions = z3.parse_smt2_string(text, ctx=ctx)
    except z3.Z3Exception as error:
        raise ConversionError(f'Z3 parse failed: {error}') from error
    return convert_assertions(assertions, timeout_ms, ackermann_limit, arbitrary_precision)

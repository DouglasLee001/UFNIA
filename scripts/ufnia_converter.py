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


_UF_OBSERVER_PREFIX = '__ufnia_observe_'


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
                                  arbitrary_precision=True, _uf_state=self.uf_state,
                                  uf_observers=self.uf_state['mode'] == 'observed',
                                  cnf=self.uf_state.get('cnf', True),
                                  post_lower_eqs=self.uf_state.get('post_lower_eqs', False),
                                  backend=self.uf_state.get('backend', 'core'))


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
        self.observations = None
        self.boolean_values = {}
        self.observation_leaves = {}

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

    def fresh_boolean(self, purpose: str) -> str:
        name = f'b_{len(self.symbol_map)}'
        self.symbol_map[name] = {'source': None, 'sort': 'Bool', 'purpose': purpose}
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
        self._encode_definitions(self.definitions)

    def _encode_definitions(self, definitions):
        for poly in definitions:
            atom = self.atom('=', poly)
            if atom is False:
                raise ConversionError('inconsistent internal product definition')
            if atom is not True:
                self.clauses.append([atom])

    @staticmethod
    def _negate(reference):
        return not reference if type(reference) is bool else -reference

    def _clause(self, references):
        refs = []
        for reference in references:
            if reference is True:
                return
            if reference is not False:
                if -reference in refs:
                    return
                if reference not in refs:
                    refs.append(reference)
        if not refs:
            raise ConversionError('inconsistent internal Boolean observation definition')
        self.clauses.append(refs)

    def _boolean_gate(self, output, kind, children):
        neg = self._negate
        if kind == z3.Z3_OP_NOT:
            self._clause((output, children[0]))
            self._clause((neg(output), neg(children[0])))
        elif kind in (z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_IMPLIES):
            if kind == z3.Z3_OP_IMPLIES:
                children = [neg(children[0]), children[1]]
                kind = z3.Z3_OP_OR
            if kind == z3.Z3_OP_AND:
                for child in children:
                    self._clause((neg(output), child))
                self._clause([output] + [neg(child) for child in children])
            else:
                for child in children:
                    self._clause((output, neg(child)))
                self._clause([neg(output)] + list(children))
        elif kind in (z3.Z3_OP_EQ, z3.Z3_OP_IFF, z3.Z3_OP_XOR, z3.Z3_OP_DISTINCT):
            if kind == z3.Z3_OP_DISTINCT and len(children) != 2:
                self._clause((output if len(children) < 2 else neg(output),))
                return
            if kind == z3.Z3_OP_XOR and len(children) > 2:
                current = children[0]
                for index, child in enumerate(children[1:], 1):
                    target = output if index == len(children) - 1 else self.store_atom(
                        ('bool', self.fresh_boolean('UF observation XOR gate')))
                    self._boolean_gate(target, z3.Z3_OP_XOR, (current, child))
                    current = target
                return
            if len(children) != 2:
                raise ConversionError('unsupported residual Boolean observation arity')
            if kind in (z3.Z3_OP_XOR, z3.Z3_OP_DISTINCT):
                output = neg(output)
            a, b = children
            self._clause((neg(output), neg(a), b))
            self._clause((neg(output), a, neg(b)))
            self._clause((output, a, b))
            self._clause((output, neg(a), neg(b)))
        elif kind == z3.Z3_OP_ITE:
            guard, yes, no = children
            self._clause((neg(guard), neg(output), yes))
            self._clause((neg(guard), output, neg(yes)))
            self._clause((guard, neg(output), no))
            self._clause((guard, output, neg(no)))
        else:
            raise ConversionError('unsupported residual Boolean observation operator')

    def _boolean_reference(self, expression):
        pending = [(expression, False)]
        gates = {z3.Z3_OP_NOT, z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_IMPLIES,
                 z3.Z3_OP_EQ, z3.Z3_OP_IFF, z3.Z3_OP_XOR, z3.Z3_OP_DISTINCT, z3.Z3_OP_ITE}
        while pending:
            expr, ready = pending.pop()
            key, kind = expr.get_id(), expr.decl().kind()
            if key in self.boolean_values:
                continue
            compound = kind in gates and all(z3.is_bool(arg) for arg in expr.children())
            if not compound:
                self.boolean_values[key] = self.literal(expr)
                continue
            if not ready:
                pending.append((expr, True))
                pending.extend((child, False) for child in reversed(expr.children()))
                continue
            children = [self.boolean_values[child.get_id()] for child in expr.children()]
            output = self.store_atom(('bool', self.fresh_boolean('UF observation Boolean gate')))
            self._boolean_gate(output, kind, children)
            self.boolean_values[key] = output
        return self.boolean_values[expression.get_id()]

    def observation_leaf(self, expr):
        key = expr.get_id()
        if key in self.observation_leaves:
            return self.observation_leaves[key]
        if z3.is_int(expr):
            name = self.materialize(self.polynomial(expr))
        elif z3.is_bool(expr):
            reference = self._boolean_reference(expr)
            atom = self.atoms[reference - 1] if type(reference) is not bool and reference > 0 else None
            if atom and atom[0] == 'bool':
                name = atom[1]
            else:
                name = self.fresh_boolean('UF observation Boolean value')
                output = self.store_atom(('bool', name))
                self._clause((self._negate(output), reference))
                self._clause((output, self._negate(reference)))
        else:
            raise ConversionError('unsupported UF observation sort')
        self.observation_leaves[key] = name
        return name

    def encode_observations(self, observations):
        """Materialize rewritten terms without changing the Goal converter."""
        self.observations, functions = [], {}
        start = len(self.definitions)
        for observation in observations:
            decl = observation.decl().get_id()
            fid = functions.setdefault(decl, len(functions))
            leaves = [(str(expr.sort()), self.observation_leaf(expr)) for expr in observation.children()]
            self.observations.append((fid, leaves))
        self._encode_definitions(self.definitions[start:])

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
        lines.append('0')
        if self.observations is not None:
            lines.append(f'UFNIA_OBSERVERS 1 {len(self.observations)}')
            for fid, leaves in self.observations:
                lines.append(' '.join([str(fid), str(len(leaves) - 1)] +
                                      [part for leaf in leaves for part in leaf]))
        return '\n'.join(lines + [''])

    def smt2_text(self):
        lines = ['(set-logic QF_UFNIA)' if self.observations is not None else '(set-logic QF_NIA)']
        lines += [f'(declare-fun {name} () {info["sort"]})' for name, info in self.symbol_map.items()]
        if self.observations is not None:
            functions = {}
            for fid, leaves in self.observations:
                functions.setdefault(fid, [sort for sort, _ in leaves])
            for fid, sorts in functions.items():
                lines.append(f'(declare-fun {_UF_OBSERVER_PREFIX}native_{fid} ({" ".join(sorts)}) Bool)')
        for clause in self.clauses:
            parts = [self.body(self.atoms[abs(ref) - 1], False) for ref in clause]
            parts = [f'(not {part})' if ref < 0 else part for ref, part in zip(clause, parts)]
            lines.append('(assert (or ' + ' '.join(parts) + '))')
        if self.observations is not None:
            for fid, leaves in self.observations:
                names = ' '.join(name for _, name in leaves)
                lines.append(f'(assert ({_UF_OBSERVER_PREFIX}native_{fid} {names}))')
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
    observed = state['mode'] == 'observed'
    return {'uf_mode': state['mode'], 'uf_pair_upper_bound': state['pair_bound'],
            'uf_declarations': declarations, 'uf_applications': applications,
            'uf_observers': observed,
            **({'uf_observer_prefix': _UF_OBSERVER_PREFIX} if observed else {}),
            'requires_congruence_refinement': state['mode'] == 'lazy',
            'intermediate_semantics': ('normalized SMT-LIB plus live UF observer records; SAT requires congruence checking'
                                       if observed else 'normalized SMT-LIB plus this UF application table; SAT requires congruence checking'
                                       if state['mode'] == 'lazy' else 'equisatisfiable normalized SMT-LIB')}


def _uf_observation_assertions(applications):
    """Record live UF terms in the same goal rewritten by every tactic.

    The final argument is the abstract function result. Fresh declarations
    prevent user function names from being interpreted as internal records.
    """
    declarations, observations = {}, []
    for decl, args, value in applications:
        key = decl.get_id()
        if key not in declarations:
            sorts = [decl.domain(i) for i in range(decl.arity())] + [decl.range()]
            domain = (z3.Sort * len(sorts))(*(sort.ast for sort in sorts))
            boolean = z3.BoolSort(decl.ctx)
            declarations[key] = z3.FuncDeclRef(z3.Z3_mk_fresh_func_decl(
                decl.ctx_ref(), _UF_OBSERVER_PREFIX, len(sorts), domain, boolean.ast), decl.ctx)
        observations.append(declarations[key](*args, value))
    return tuple(observations)


def convert_assertions(assertions, timeout_ms=30_000, ackermann_limit=100_000,
                       arbitrary_precision=False, _uf_state=None, uf_observers=False, cnf=True,
                       post_lower_eqs=False, backend='core') -> ConversionResult:
    if _uf_state is not None:
        uf_observers = _uf_state['mode'] == 'observed'
        cnf = _uf_state.get('cnf', cnf)
        post_lower_eqs = _uf_state.get('post_lower_eqs', post_lower_eqs)
        backend = _uf_state.get('backend', backend)
    if backend not in ('core', 'mature'):
        raise ConversionError(f'unsupported backend {backend!r}')
    if backend == 'mature':
        if not arbitrary_precision:
            raise ConversionError('mature backend requires the arbitrary-precision target')
        if not cnf:
            raise ConversionError('mature backend requires CNF on')
    if uf_observers and not arbitrary_precision:
        raise ConversionError('UF observers require the arbitrary-precision target')
    if not cnf and not (arbitrary_precision and uf_observers):
        raise ConversionError('CNF off requires UF observers and the arbitrary-precision target')
    if post_lower_eqs and not arbitrary_precision:
        raise ConversionError('post-lowering equation elimination requires the arbitrary-precision target')
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
            uf_observers = state['mode'] == 'observed'
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
                eager = not uf_observers and pair_bound <= min(ackermann_limit, 10_000)
                lemmas = []
                if eager:
                    grouped = {}
                    for decl, args, value in applications:
                        for previous_args, previous_value in grouped.get(decl.get_id(), []):
                            lemmas.append(z3.Implies(z3.And(*(a == b for a, b in zip(args, previous_args))), value == previous_value))
                        grouped.setdefault(decl.get_id(), []).append((args, value))
                assertions += tuple(lemmas)
                observations = ()
                if uf_observers:
                    observations = _uf_observation_assertions(applications)
                    assertions += observations
                state = {'applications': applications, 'base': assertions, 'original': original_assertions,
                         'carrier_encoding': carrier_encoding, 'pair_bound': pair_bound,
                         'mode': 'observed' if uf_observers else 'eager' if eager else 'lazy', 'timeout_ms': timeout_ms,
                         'ackermann_limit': ackermann_limit, 'cnf': bool(cnf),
                         'post_lower_eqs': bool(post_lower_eqs), 'backend': backend,
                         'observer_declarations': {expr.decl().get_id() for expr in observations}}
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
        if post_lower_eqs:
            steps += [optional('propagate-values'), optional('solve-eqs')]
        if not arbitrary_precision:
            steps.append(z3.With(tactic('ackermannize_bv'), div0_ackermann_limit=ackermann_limit))
        steps.append(z3.With(tactic('simplify'), arith_lhs=True, blast_distinct=True))
        if cnf:
            steps.append(tactic('tseitin-cnf'))
        chain = z3.Then(*steps, ctx=ctx)
        result = z3.TryFor(chain, max(1, int(timeout_ms)))(goal)
        trace('tactics_complete')
        if len(result) != 1 or result[0].precision() != z3.Z3_GOAL_PRECISE:
            raise ConversionError('preprocessor did not produce one precise goal')
        if arbitrary_precision and backend == 'core':
            # The GMP evaluator accepts general polynomial ASTs. Do not run
            # the reference reader's Python atom formatter over huge DAGs.
            from UFNIA_LS.native_frontend import analyze_normalized, serialize_normalized
            try:
                symbols, atom_count, normalized_nodes = analyze_normalized(result[0], uf_observers=uf_observers)
                trace('residual_checked')
                normalized = serialize_normalized(result[0], symbols, uf_observers=uf_observers)
            except NativeFrontendError as error:
                raise ConversionError(str(error)) from error
            native = normalized
            native_symbols = {('b_' if z3.is_bool(expr) else 'i_') + str(i): expr for i, expr in enumerate(symbols)}
            symbol_map = {name: {'source': str(expr.decl().name()), 'sort': str(expr.sort())} for name, expr in native_symbols.items()}
            clause_count, product_count = len(result[0]), 0
        else:
            observer_declarations = state.get('observer_declarations', set()) if uf_observers and state else set()
            observations = tuple(expr for expr in result[0] if expr.decl().get_id() in observer_declarations)
            observation_ids = {expr.get_id() for expr in observations}
            for expr in _nodes(result[0], max_nodes=None if arbitrary_precision else 1_000_000):
                if z3.is_quantifier(expr) or not z3.is_app(expr):
                    raise ConversionError('unsupported residual quantified expression')
                if expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and expr.num_args():
                    if expr.get_id() not in observation_ids:
                        raise ConversionError(f'UF elimination incomplete (Ackermann resource limit {ackermann_limit}); residual function {expr.decl().name()}')
                if expr.decl().kind() in (z3.Z3_OP_IDIV, z3.Z3_OP_MOD, z3.Z3_OP_REM):
                    raise ConversionError('integer div/mod elimination incomplete')
            writer = _NativeWriter(arbitrary_precision=arbitrary_precision)
            trace('residual_checked')
            writer.encode(expr for expr in result[0] if expr.get_id() not in observation_ids)
            if uf_observers:
                writer.encode_observations(observations)
            trace('native_encoded')
            native, normalized = writer.native_text(), writer.smt2_text()
            native_symbols, symbol_map = writer.native_symbols, writer.symbol_map
            atom_count, clause_count, product_count = len(writer.atoms), len(writer.clauses), writer.product_count
            normalized_nodes = None
        trace('text_serialized')
        metadata = {
            'format': ('mature GMP atom-table/CNF' if backend == 'mature' else
                       ('QF_UFNIA SMT-LIB with UF observers' if uf_observers else 'QF_NIA SMT-LIB')
                       if arbitrary_precision else 'NIA_LS atom-table/CNF'),
            'backend': backend,
            'arbitrary_precision': arbitrary_precision, 'carrier_sort_encoding': carrier_encoding,
            'uf_observers': bool(uf_observers),
            'cnf': 'on' if cnf else 'off',
            'post_lower_eqs': 'on' if post_lower_eqs else 'off',
            'frontend_engine': 'python_native_writer_mpz' if backend == 'mature' else 'native_z3_dag' if arbitrary_precision else 'python_legacy',
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
        if backend == 'mature':
            metadata['native_format_version'] = 1
            if uf_observers:
                metadata['native_uf_observer_format_version'] = 1
                metadata['native_uf_observer_count'] = len(writer.observations)
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

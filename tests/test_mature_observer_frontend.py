"""Real mature v2 frontend/reader/model tests, without complete SMT search."""
import contextlib
import io
import itertools
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import z3

from scripts.ufnia_converter import _NativeWriter, convert_assertions
from UFNIA_LS import run as driver


class MatureObserverFrontendTests(unittest.TestCase):
    def convert(self, assertions):
        return convert_assertions(assertions, arbitrary_precision=True,
                                  backend='mature', uf_observers=True)

    def records(self, conversion):
        lines = conversion.native.splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith('UFNIA_OBSERVERS '))
        _, version, count = lines[index].split()
        self.assertEqual(version, '1')
        rows = []
        for line in lines[index + 1:]:
            if not line.strip():
                continue
            fields = line.split()
            fid, arity = map(int, fields[:2])
            self.assertEqual(len(fields), 2 + 2 * (arity + 1))
            leaves = list(zip(fields[2::2], fields[3::2]))
            for sort, name in leaves:
                self.assertIn(sort, ('Int', 'Bool'))
                self.assertEqual(conversion.metadata['symbol_map'][name]['sort'], sort)
            rows.append((fid, arity, leaves))
        self.assertEqual(len(rows), int(count))
        return rows

    def invoke(self, formula, *options, seconds='.35', parse_only=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report = directory / 'input.smt2', directory / 'report.json'
            model, keep = directory / 'model.smt2', directory / 'kept'
            source.write_text(formula + '(check-sat)')
            args = [str(source), '--backend', 'mature', '--uf-observers',
                    '--seconds', seconds, '--json-report', str(report),
                    '--keep-dir', str(keep), '--model-output', str(model), *options]
            if parse_only:
                args.append('--parse-only')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = driver.main(args)
            self.assertEqual(code, 0, err.getvalue())
            info = json.loads(report.read_text())
            query = info['last_query']
            native = Path(query['artifacts']['native']).read_text()
            return out.getvalue(), info, native, model.read_text() if model.exists() else None

    def assert_v2_sat(self, result):
        out, info, native, model = result
        self.assertEqual(out.strip(), 'sat')
        query = info['last_query']
        self.assertTrue(query['validated'])
        self.assertTrue(query['original_model_validated'])
        self.assertTrue(query['core']['validated'])
        self.assertEqual(query['core']['backend_version'], 'mature-mpz-v2')
        self.assertEqual(info['backend_version'], 'mature-mpz-v2')
        self.assertEqual(info['backend_configuration'], 'mature_uf_observed')
        self.assertEqual(query['rounds'], 1)
        self.assertEqual(query['refinement_lemmas'], 0)
        self.assertEqual(query['preprocessing']['uf_mode'], 'observed')
        self.assertEqual(query['normalized_semantics'], 'abstraction_with_congruence_table')
        self.assertIn('UFNIA_OBSERVERS 1 ', native)
        self.assertIsNotNone(model)

    def test_composite_int_arguments_and_eliminated_results_are_materialized(self):
        x, y = z3.Ints('observed_mature_x observed_mature_y')
        f = z3.Function('observed_mature_f', z3.IntSort(), z3.IntSort())
        conversion = self.convert((x*x == 9, y == x + 2, f(x*y) == x+y, f(1) == 0))
        rows = self.records(conversion)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row[0] for row in rows}), 1)
        self.assertTrue(all(row[1] == 1 for row in rows))
        purposes = [value.get('purpose', '') for value in conversion.metadata['symbol_map'].values()]
        self.assertTrue(any('operand' in value or 'observation' in value for value in purposes))
        self.assertTrue(all(z3.is_const(expr) for expr in conversion.native_symbols.values()))
        self.assertTrue(all(value.get('source') is not None
                            for name, value in conversion.metadata['symbol_map'].items()
                            if name in conversion.native_symbols))
        # The original Goal still contains live rewritten arguments/results;
        # auxiliary model values must never replace the Goal's converter.
        self.assertTrue(any(expr.decl().name().startswith('__ufnia_observe_')
                            for expr in conversion.goal))

    def test_real_composite_int_model_restores_original_formula(self):
        formula = '''(declare-const x Int)(declare-const y Int)
(declare-fun f (Int) Int)
(assert (= (* x x) 9))(assert (= y (+ x 2)))
(assert (= (f (* x y)) (+ x y)))(assert (= (f 1) 0))'''
        self.assert_v2_sat(self.invoke(formula))

    def test_real_bool_arguments_results_and_nested_functions(self):
        formula = '''(declare-const x Int)(declare-const y Int)
(declare-fun pred (Int) Bool)(declare-fun f (Bool) Int)
(assert (>= x 1))(assert (>= y 1))
(assert (= (pred x) (and (> x 0) (> y 0))))
(assert (= (f (pred x)) 7))(assert (= (f false) 8))'''
        self.assert_v2_sat(self.invoke(formula))

    def test_real_observer_only_free_arguments_are_searchable_without_fake_bounds(self):
        x, y = z3.Ints('observer_only_x observer_only_y')
        f = z3.Function('observer_only_f', z3.IntSort(), z3.IntSort())
        conversion = self.convert((f(x) == 0, f(y) == 1))
        rows = self.records(conversion)
        source_names = {name for name, expr in conversion.native_symbols.items()
                        if z3.eq(expr, x) or z3.eq(expr, y)}
        self.assertEqual(len(source_names), 2)
        self.assertTrue(source_names.issubset({name for _, _, leaves in rows for _, name in leaves}))
        # No CNF atom may constrain otherwise free x/y just to register them.
        before_table = conversion.native.split('UFNIA_OBSERVERS ', 1)[0]
        for name in source_names:
            self.assertNotIn(name, before_table.split())
        formula = '''(declare-const x Int)(declare-const y Int)(declare-fun f (Int) Int)
(assert (= (f x) 0))(assert (= (f y) 1))'''
        self.assert_v2_sat(self.invoke(formula, '--interaction', 'joint'))

    def test_real_pure_uf_collision_cannot_publish_sat(self):
        formula = '''(declare-const x Int)(declare-const y Int)(declare-fun f (Int) Int)
(assert (= x y))(assert (= (f x) 0))(assert (= (f y) 1))'''
        for scoring in ('count', 'paws'):
            with self.subTest(scoring=scoring):
                out, info, _, model = self.invoke(formula, '--scoring', scoring, seconds='.03')
                self.assertEqual(out.strip(), 'unknown')
                self.assertFalse(info['last_query']['validated'])
                self.assertFalse(info['last_query']['core']['validated'])
                self.assertIsNone(model)

    def test_real_source_prefix_collision_remains_a_source_function(self):
        formula = '''(declare-fun __ufnia_observe_user (Int) Int)
(assert (= (__ufnia_observe_user 0) 7))
(assert (= (__ufnia_observe_user 1) 8))'''
        result = self.invoke(formula)
        self.assert_v2_sat(result)
        self.assertIn('define-fun __ufnia_observe_user', result[3])

    def test_actual_parse_only_options_and_zero_application_v2(self):
        for interaction in ('none', 'soft', 'joint'):
            for scoring in ('count', 'paws'):
                with self.subTest(interaction=interaction, scoring=scoring):
                    _, info, native, _ = self.invoke('(assert true)', '--interaction', interaction,
                                                    '--scoring', scoring, parse_only=True)
                    query = info['last_query']
                    self.assertEqual(query['status'], 'parsed')
                    self.assertEqual(query['core']['backend_version'], 'mature-mpz-v2')
                    self.assertIn('UFNIA_OBSERVERS 1 0', native)
                    self.assertEqual(info['effective_options']['interaction'], interaction)
                    self.assertEqual(info['effective_options']['scoring'], scoring)
                    self.assertIsNone(info['effective_options']['search_phase'])
                    self.assertEqual(query['interaction'], interaction)
                    self.assertEqual(query['scoring'], scoring)

    def test_boolean_reification_exhaustive_truth_tables_and_shared_cache(self):
        b, c, guard = z3.Bools('gate_b gate_c gate_guard')
        x = z3.Int('gate_int')
        expressions = [b, z3.BoolVal(True), z3.BoolVal(False), z3.Not(b),
                       z3.And(b, c, guard), z3.Or(b, c, guard), z3.Implies(b, c),
                       b == c, z3.Distinct(b, c), z3.Distinct(b, c, guard),
                       z3.Xor(b, c), z3.If(guard, b, c),
                       z3.If(guard, x > 0, x == 0),
                       z3.And(b, z3.Xor(c, guard), z3.Not(x > 0))]
        marker = z3.Function('truth_table_marker', z3.BoolSort(), z3.BoolSort(), z3.BoolSort())
        for expression in expressions:
            with self.subTest(expression=expression.sexpr()):
                writer = _NativeWriter(True)
                writer.encode(())
                writer.encode_observations((marker(expression, z3.BoolVal(True)),))
                target = writer.observations[0][1][0][1]
                saved = (len(writer.clauses), len(writer.symbol_map))
                self.assertEqual(writer.observation_leaf(expression), target)
                self.assertEqual((len(writer.clauses), len(writer.symbol_map)), saved)
                auxiliary = [name for name, info in writer.symbol_map.items() if info['source'] is None]
                self.assertTrue(all(writer.symbol_map[name]['sort'] == 'Bool' for name in auxiliary))
                for bits in itertools.product((False, True), repeat=3):
                    for integer in (-1, 0, 1):
                        source = dict(zip((b.get_id(), c.get_id(), guard.get_id()), bits))
                        source[x.get_id()] = integer
                        assignments = {name: source[expr.get_id()] for name, expr in writer.native_symbols.items()}
                        substitutions = [(var, z3.BoolVal(value)) for var, value in zip((b, c, guard), bits)]
                        expected = z3.is_true(z3.simplify(z3.substitute(
                            expression, *substitutions, (x, z3.IntVal(integer)))))
                        outputs = set()
                        for extension in itertools.product((False, True), repeat=len(auxiliary)):
                            values = dict(assignments)
                            values.update(zip(auxiliary, extension))
                            truths = []
                            for atom in writer.atoms:
                                if atom[0] == 'bool':
                                    truths.append(values[atom[1]])
                                else:
                                    relation, terms, rhs = atom
                                    lhs = sum(coefficient * math.prod(values[name] for name in term)
                                              for term, coefficient in terms)
                                    truths.append(lhs == rhs if relation == '=' else lhs <= rhs)
                            if all(any(truths[abs(ref)-1] == (ref > 0) for ref in clause)
                                   for clause in writer.clauses):
                                outputs.add(values[target])
                        self.assertEqual(outputs, {expected}, (expression, bits, integer))

    def test_real_core_option_evidence_cannot_disagree_with_requested_policy(self):
        binary = str(driver.MATURE_CORE.resolve())
        for option, replacement in (('--interaction', 'none'), ('--scoring', 'paws')):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                wrapper = Path(temporary) / 'actual-core-with-modified-policy'
                wrapper.write_text('#!/usr/bin/env python3\nimport os, sys\n'
                                   'args = sys.argv[1:]\n'
                                   f'args[args.index({option!r}) + 1] = {replacement!r}\n'
                                   f'os.execv({binary!r}, [{binary!r}, *args])\n')
                wrapper.chmod(0o755)
                with patch.object(driver, 'MATURE_CORE', wrapper):
                    out, info, _, model = self.invoke('(assert true)')
                self.assertEqual(out.strip(), 'unknown')
                self.assertFalse(info['last_query']['validated'])
                self.assertIn('evidence', info['last_query']['reason'])
                self.assertIsNone(model)


if __name__ == '__main__':
    unittest.main()

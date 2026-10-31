"""The mature native bridge preserves GMP values, models, and UF refinement."""
import contextlib
import hashlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import z3

from scripts.ufnia_converter import ConversionError, convert_assertions


class MatureFrontendTests(unittest.TestCase):
    VERSION = 'mature-mpz-v1'

    def convert(self, assertions, **options):
        self.assertIn('backend', inspect.signature(convert_assertions).parameters)
        return convert_assertions(assertions, arbitrary_precision=True, backend='mature', **options)

    def driver(self):
        from UFNIA_LS import run
        self.assertIn('--backend', inspect.getsource(run._arguments))
        return run

    def fake_core(self, directory, behavior='normal'):
        core = directory / 'fake-mature'
        core.write_text('BEHAVIOR = %r\n' % behavior + '''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
native = pathlib.Path(args[0])
assert native.suffix == '.native'
text = native.read_text()
assert text.splitlines()[0].isdigit() and '(set-logic' not in text
metadata = json.loads(native.with_suffix('.metadata.json').read_text())
assert metadata['backend'] == 'mature' and metadata['arbitrary_precision']
allowed = {'--parse-only', '--seconds', '--seed', '--model-output'}
assert all(argument in allowed for argument in args[1:] if argument.startswith('--'))
stats = {'backend': 'mature_mpz', 'backend_version': 'mature-mpz-v1', 'steps': 0}
if '--parse-only' in args:
    stats['status'] = 'parsed'
    print(json.dumps(stats))
else:
    assert '--seconds' in args and '--seed' in args and '--model-output' in args
    impossible = any(info.get('purpose') == 'constant false witness' for info in metadata['symbol_map'].values())
    status = 'unknown' if impossible else 'sat'
    values = {}; application = 0
    for name, info in metadata['symbol_map'].items():
        if info['sort'] == 'Bool': values[name] = False
        elif str(info.get('source', '')).startswith('ufnia_app'):
            application += 1; values[name] = str(application)
        else: values[name] = '0'
    if status == 'sat':
        output = pathlib.Path(args[args.index('--model-output') + 1])
        output.write_text(json.dumps(values))
    stats.update(status=status, validated=status == 'sat')
    if BEHAVIOR == 'missing_backend': stats.pop('backend')
    if BEHAVIOR == 'wrong_version': stats['backend_version'] = 'wrong'
    print(status)
    print(json.dumps(stats), file=sys.stderr)
''')
        # The shebang must remain the first line for a directly executed fake.
        text = core.read_text().replace("#!/usr/bin/env python3\n", '')
        core.write_text('#!/usr/bin/env python3\n' + text)
        core.chmod(0o755)
        return core

    def test_native_gmp_output_and_source_only_model_map(self):
        x, y = z3.Ints('mature_source_x mature_source_y')
        huge = (1 << 300) + 17
        assertions = (huge*x + y >= huge + 3, x*y == huge + 19)
        conversion = self.convert(assertions)
        self.assertTrue(conversion.native.splitlines()[0].isdigit())
        self.assertNotIn('(set-logic', conversion.native)
        self.assertIn(str(huge), conversion.native)
        self.assertIn(str(huge + 19), conversion.native)
        self.assertEqual(conversion.metadata['backend'], 'mature')
        self.assertEqual(conversion.metadata['native_format_version'], 1)
        self.assertGreater(conversion.metadata['binary_product_count'], 0)
        self.assertTrue(all(z3.eq(expr, x) or z3.eq(expr, y) for expr in conversion.native_symbols.values()))
        values = {name: '1' if z3.eq(expr, x) else str(huge + 19) for name, expr in conversion.native_symbols.items()}
        _, valid = conversion.restore_model(values)
        self.assertTrue(valid)

    def test_refinement_keeps_mature_native_conversion(self):
        x, y, z = z3.Ints('mature_uf_x mature_uf_y mature_uf_z')
        f = z3.Function('mature_uf_f', z3.IntSort(), z3.IntSort())
        conversion = self.convert((x == y, z3.Distinct(f(x), f(y), f(z))), ackermann_limit=1)
        self.assertEqual(conversion.metadata['uf_mode'], 'lazy')
        refined = conversion.refine((z3.BoolVal(True),))
        self.assertEqual(refined.metadata['backend'], 'mature')
        self.assertTrue(refined.native.splitlines()[0].isdigit())
        self.assertIs(refined.uf_state, conversion.uf_state)

    def test_noncnf_or_legacy_targets_are_rejected(self):
        self.assertIn('backend', inspect.signature(convert_assertions).parameters)
        for options in ({'arbitrary_precision': True, 'cnf': False},
                        {'arbitrary_precision': False}):
            with self.assertRaises(ConversionError):
                convert_assertions((z3.BoolVal(True),), backend='mature', **options)

    def test_driver_parse_protocol_backend_evidence_and_intrinsic_options(self):
        driver = self.driver()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, keep = directory / 'input.smt2', directory / 'kept'
            source.write_text('(declare-const x Int)(assert (> (* x x) %d))(check-sat)' % (1 << 300))
            core = self.fake_core(directory)
            output, error = io.StringIO(), io.StringIO()
            with patch.object(driver, 'MATURE_CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--backend', 'mature', '--parse-only', '--keep-dir', str(keep)])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            query = report['last_query']
            self.assertEqual(report['backend'], 'mature')
            self.assertEqual(query['backend'], 'mature')
            self.assertEqual(report['backend_configuration'], 'mature_intrinsic')
            self.assertEqual(query['backend_version'], self.VERSION)
            self.assertEqual(report['backend_version'], self.VERSION)
            self.assertEqual(report['core_binary'], str(core))
            self.assertEqual(report['core_sha256'], hashlib.sha256(core.read_bytes()).hexdigest())
            self.assertEqual(query['core_sha256'], report['core_sha256'])
            self.assertTrue(Path(query['artifacts']['native']).is_file())
            self.assertEqual(report['requested_options']['scoring'], 'count')
            self.assertIsNone(report['effective_options']['scoring'])
            self.assertIsNone(query['scoring'])
            self.assertIsNone(query['search_phase'])
            self.assertEqual(query['effective_options'], report['effective_options'])

    def test_mature_rejects_unsupported_search_knobs_without_silent_ignoring(self):
        driver = self.driver()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'input.smt2'
            source.write_text('(assert true)(check-sat)')
            for options in (('--cnf', 'off'), ('--scoring', 'paws'),
                            ('--search-policy', 'greedy'), ('--candidate-policy', 'critical'),
                            ('--uf-observers', '--distance', 'structural')):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--backend', 'mature', *options])
                self.assertEqual(code, 2)
                self.assertIn('mature', error.getvalue())

    def test_mature_multiple_queries_models_and_final_export_hash(self):
        driver = self.driver()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report_path, model = directory / 'input.smt2', directory / 'report.json', directory / 'model.smt2'
            huge = (1 << 300) + 33
            source.write_text('(declare-const x Int)(assert (= x %d))(check-sat)(push 1)(assert (distinct x %d))(check-sat)(pop 1)(check-sat)' % (huge, huge))
            core = self.fake_core(directory)
            output, error = io.StringIO(), io.StringIO()
            with patch.object(driver, 'MATURE_CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--backend', 'mature', '--seconds', '.5',
                                    '--model-output', str(model), '--json-report', str(report_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().split(), ['sat', 'unknown', 'sat'])
            report = json.loads(report_path.read_text())
            self.assertEqual(report['queries_processed'], 3)
            self.assertEqual(report['counts']['sat'], 2)
            self.assertEqual(report['model_output_sha256'], hashlib.sha256(model.read_bytes()).hexdigest())
            self.assertIn(str(huge), model.read_text())

    def test_lazy_uf_collision_refines_without_publishing_false_sat(self):
        driver = self.driver()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report_path = directory / 'input.smt2', directory / 'report.json'
            source.write_text('(declare-const x Int)(declare-const y Int)(declare-const z Int)(declare-fun f (Int) Int)(assert (= x y))(assert (distinct (f x) (f y) (f z)))(check-sat)')
            core = self.fake_core(directory)
            output, error = io.StringIO(), io.StringIO()
            with patch.object(driver, 'MATURE_CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--backend', 'mature', '--ackermann-limit', '1',
                                    '--seconds', '.5', '--json-report', str(report_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().strip(), 'unknown')
            query = json.loads(report_path.read_text())['last_query']
            self.assertGreaterEqual(query['rounds'], 2)
            self.assertGreater(query['refinement_lemmas'], 0)
            self.assertEqual(query['preprocessing']['backend'], 'mature')
            self.assertFalse(query['validated'])

    def test_missing_backend_identity_or_invalid_model_cannot_become_sat(self):
        driver = self.driver()
        for behavior, formula in (('missing_backend', '(assert true)'),
                                  ('wrong_version', '(assert true)'),
                                  ('normal', '(declare-const x Int)(assert (= (* x x) 9))')):
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(formula + '(check-sat)')
                core = self.fake_core(directory, behavior)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'MATURE_CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--backend', 'mature', '--seconds', '.2', '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), 'unknown')
                self.assertFalse(json.loads(report_path.read_text())['last_query']['validated'])


if __name__ == '__main__':
    unittest.main()

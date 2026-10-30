"""The native pass must preserve the Python UF abstraction and AST ownership."""
import gc
from pathlib import Path
import subprocess
import unittest

import z3

ROOT = Path(__file__).resolve().parents[1]


class NativeFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "-C", str(ROOT / "UFNIA_LS"), "all"],
                       check=True, capture_output=True, text=True)
        from UFNIA_LS import native_frontend
        cls.native = native_frontend

    def assert_matches_python(self, assertions):
        from scripts.ufnia_converter import _abstract_functions, _check_input
        _check_input(assertions, max_nodes=None)
        expected, old_apps = _abstract_functions(assertions)
        actual, new_apps = self.native.prepare(assertions)
        self.assertEqual(len(new_apps), len(old_apps))
        replacements = tuple((new[2], old[2]) for new, old in zip(new_apps, old_apps))
        for new, old in zip(actual, expected):
            self.assertTrue(z3.eq(z3.substitute(new, *replacements), old))
        for new, old in zip(new_apps, old_apps):
            self.assertEqual(new[0].get_id(), old[0].get_id())
            self.assertEqual(len(new[1]), len(old[1]))
            for new_arg, old_arg in zip(new[1], old[1]):
                self.assertTrue(z3.eq(z3.substitute(new_arg, *replacements), old_arg))
        return actual, new_apps

    def test_nested_uf_congruence_abstraction_matches_python(self):
        ctx = z3.Context()
        x, y = z3.Ints("x y", ctx=ctx)
        f = z3.Function("f", z3.IntSort(ctx), z3.IntSort(ctx))
        actual, apps = self.assert_matches_python((x == y, f(f(x)) != f(f(y))))
        self.assertEqual(len(apps), 4)
        self.assertTrue(all(not expr.children() or expr.decl().kind() != z3.Z3_OP_UNINTERPRETED
                            for expr in actual))

    def test_bool_uf_bigint_divmod_and_shared_ast(self):
        ctx = z3.Context()
        x = z3.Int("x", ctx)
        pred = z3.Function("pred", z3.IntSort(ctx), z3.BoolSort(ctx))
        f = z3.Function("f", z3.BoolSort(ctx), z3.IntSort(ctx))
        shared = f(pred(x))
        huge = (1 << 512) + 71
        assertions = (z3.Or(pred(x), shared == huge), (shared / 3) + (shared % 3) >= 0)
        _, apps = self.assert_matches_python(assertions)
        self.assertEqual(len(apps), 2)
        self.assertTrue(any(z3.is_bool(value) for _, _, value in apps))

    def test_opaque_sort_has_explicit_fallback_signal(self):
        ctx = z3.Context()
        opaque = z3.DeclareSort("Opaque", ctx)
        x = z3.Const("x", opaque)
        with self.assertRaises(self.native.UnsupportedCarrier):
            self.native.prepare((x == x,))

    def test_real_bitvector_and_quantifier_are_rejected(self):
        ctx = z3.Context()
        x = z3.Real("x", ctx)
        i = z3.Int("i", ctx)
        cases = (x > 0, z3.BitVec("v", 8, ctx) == 3, z3.ForAll([i], i == i))
        for assertion in cases:
            with self.subTest(assertion=str(assertion)):
                with self.assertRaises(self.native.UnsupportedInput):
                    self.native.prepare((assertion,))

    def test_nondefault_context_references_survive_gc(self):
        ctx = z3.Context()
        x = z3.Int("x", ctx)
        f = z3.Function("f", z3.IntSort(ctx), z3.IntSort(ctx))
        assertions, apps = self.native.prepare((f(f(x)) == 19,))
        del f, x, ctx
        gc.collect()
        for i in range(1000):
            z3.Int(f"garbage_{i}", assertions[0].ctx) + i
        gc.collect()
        self.assertEqual(len(apps), 2)
        self.assertTrue(z3.is_eq(assertions[0]))
        self.assertTrue(all(value.sort().kind() == z3.Z3_INT_SORT for _, _, value in apps))
        self.assertEqual(str(apps[0][0].name()), "f")

    def test_normalized_high_degree_bigints_quotes_roundtrip_to_core(self):
        import json
        ctx = z3.Context()
        huge = (1 << 350) + 71
        assertions = z3.parse_smt2_string(f"""
            (declare-const |name with spaces| Int)
            (declare-const |guard;quoted| Bool)
            (assert (or |guard;quoted| (= (* |name with spaces| |name with spaces| |name with spaces|) {huge})))
            (assert (= (ite |guard;quoted| |name with spaces| 7) {huge}))
        """, ctx=ctx)
        symbols, atoms, nodes = self.native.analyze_normalized(assertions)
        self.assertEqual({str(symbol.decl().name()) for symbol in symbols}, {"name with spaces", "guard;quoted"})
        self.assertEqual(atoms, 3)
        self.assertGreater(nodes, 8)
        model = z3.Model(ctx=ctx)
        for symbol in symbols:
            model.update_value(symbol.decl(), z3.BoolVal(True, ctx=ctx) if z3.is_bool(symbol) else z3.IntVal(huge, ctx=ctx))
        self.assertEqual(len(model), 2)
        serialized = self.native.serialize_normalized(assertions)
        for index, symbol in enumerate(symbols):
            self.assertIn(("b_" if z3.is_bool(symbol) else "i_") + str(index), serialized)
        self.assertNotIn("name with spaces", serialized)
        self.assertNotIn("guard;quoted", serialized)
        self.assertEqual(serialized, self.native.serialize_normalized(assertions, symbols))
        result = subprocess.run([str(ROOT / "build/ufnia_ls/ufnia_core"), "--parse-only"],
                                input=serialized, text=True, capture_output=True, check=True)
        stats = json.loads(result.stdout)
        self.assertEqual(stats["integer_variables"], 1)
        self.assertEqual(stats["boolean_variables"], 1)
        self.assertGreaterEqual(stats["max_numeral_bits"], 351)
        self.assertGreater(stats["nonlinear_multiplications"], 0)

    def test_normalized_analysis_rejects_residual_functions_divmod(self):
        ctx = z3.Context()
        x = z3.Int("x", ctx)
        f = z3.Function("f", z3.IntSort(ctx), z3.IntSort(ctx))
        for assertion in (f(x) == 0, x / 3 == 0, x % 3 == 0, z3.ForAll([x], x == x)):
            with self.subTest(assertion=str(assertion)):
                with self.assertRaises(self.native.UnsupportedInput):
                    self.native.analyze_normalized((assertion,))

    def test_normalized_canonical_names_preserve_same_name_bool_int_model(self):
        import json
        ctx = z3.Context()
        integer = z3.Int("same name", ctx)
        boolean = z3.Bool("same name", ctx)
        assertions = (z3.Or(boolean, integer > 1), z3.Not(boolean), integer == 7)
        symbols, atoms, _ = self.native.analyze_normalized(assertions)
        self.assertEqual(len(symbols), 2)
        self.assertEqual(atoms, 3)
        serialized = self.native.serialize_normalized(assertions, symbols)
        self.assertNotIn("same name", serialized)
        self.assertEqual(serialized, self.native.serialize_normalized(assertions))
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.json"
            result = subprocess.run([str(ROOT / "build/ufnia_ls/ufnia_core"), "--seconds", "0.1", "--model-output", str(model_path)],
                                    input=serialized, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), "sat", result.stderr)
            values = json.loads(model_path.read_text())
        restored = z3.Model(ctx=ctx)
        for index, symbol in enumerate(symbols):
            name = ("b_" if z3.is_bool(symbol) else "i_") + str(index)
            value = z3.BoolVal(values[name], ctx=ctx) if z3.is_bool(symbol) else z3.IntVal(values[name], ctx=ctx)
            restored.update_value(symbol.decl(), value)
        self.assertTrue(all(z3.is_true(restored.eval(assertion)) for assertion in assertions))

    def test_normalized_shared_dag_and_empty_serialization(self):
        ctx = z3.Context()
        x = z3.Int("x", ctx)
        expression = x
        for _ in range(40):
            expression = expression + expression
        assertions = (expression == 0,)
        symbols, atoms, nodes = self.native.analyze_normalized(assertions)
        self.assertEqual(len(symbols), 1)
        self.assertEqual(atoms, 1)
        self.assertLess(nodes, 50)
        serialized = self.native.serialize_normalized(assertions)
        self.assertLess(len(serialized), 10000)
        empty = self.native.serialize_normalized(())
        self.assertIn("(assert", empty)
        self.assertFalse(self.native.analyze_normalized(())[0])

    def test_astvector_empty_and_mixed_contexts(self):
        ctx = z3.Context()
        vector = z3.AstVector(ctx=ctx)
        vector.push(z3.Int("x", ctx) > 0)
        actual, apps = self.native.prepare(vector)
        self.assertEqual(len(actual), 1)
        self.assertFalse(apps)
        self.assertEqual(self.native.prepare(()), ((), ()))
        with self.assertRaises(ValueError):
            self.native.prepare((z3.Bool("b", ctx), z3.Bool("other")))


if __name__ == "__main__":
    unittest.main()

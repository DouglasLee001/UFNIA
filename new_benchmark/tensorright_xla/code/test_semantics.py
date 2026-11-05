"""Independent concrete checks for the fixed-rank tensor adapter."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

import z3

CODE = Path(__file__).resolve().parent
GEN = CODE / "generate.py"


class TensorSemanticsTests(unittest.TestCase):
    def load(self):
        self.assertTrue(GEN.is_file(), "tensor generator implementation is missing")
        spec = importlib.util.spec_from_file_location("tensor_generator", GEN)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def integer(self, expr, values=()):
        expr = z3.simplify(expr)
        expr = z3.substitute(expr, *values) if values else expr
        result = z3.simplify(expr)
        self.assertTrue(z3.is_int_value(result), str(result))
        return result.as_long()

    def test_pad_then_slice_matches_concrete_list(self):
        g = self.load()
        n = z3.IntVal
        t = g.tensor("TestA", [n(3)])
        p = g.pad_tensor(t, [n(2)], [n(1)], [n(2)], n(-9))
        # Directly materialize XLA pad: [pad,pad,A0,pad,pad,A1,pad,pad,A2,pad].
        concrete = [-9, -9, 4, -9, -9, 7, -9, -9, 11, -9]
        self.assertEqual(self.integer(p.shape[0]), len(concrete))
        bindings = tuple((t.read([n(i)]), n(v)) for i, v in enumerate([4, 7, 11]))
        for i, value in enumerate(concrete):
            self.assertEqual(self.integer(p.read([n(i)]), bindings), value)
        s = g.slice_tensor(p, [n(2)], [n(9)], [n(3)])
        self.assertEqual(self.integer(s.shape[0]), 3)
        self.assertEqual([self.integer(s.read([n(i)]), bindings) for i in range(3)], [4, 7, 11])

    def test_reverse_and_strided_slice_match_python(self):
        g = self.load()
        n = z3.IntVal
        t = g.tensor("TestB", [n(7)])
        expr = g.slice_tensor(g.reverse_tensor(t, [0]), [n(1)], [n(7)], [n(2)])
        data = [10, 20, 30, 40, 50, 60, 70]
        bindings = tuple((t.read([n(i)]), n(v)) for i, v in enumerate(data))
        expected = list(reversed(data))[1:7:2]
        self.assertEqual(self.integer(expr.shape[0]), len(expected))
        self.assertEqual([self.integer(expr.read([n(i)]), bindings) for i in range(len(expected))], expected)

    def test_concat_read_branches_and_bounds(self):
        g = self.load()
        n = z3.IntVal
        a, b = g.tensor("TestC", [n(2)]), g.tensor("TestD", [n(3)])
        c = g.concat_tensor(a, b, 0)
        bindings = tuple([(a.read([n(i)]), n(v)) for i, v in enumerate([3, 4])] +
                         [(b.read([n(i)]), n(v)) for i, v in enumerate([5, 6, 7])])
        self.assertEqual(self.integer(c.shape[0]), 5)
        self.assertEqual([self.integer(c.read([n(i)]), bindings) for i in range(5)], [3, 4, 5, 6, 7])
        bad = g.slice_tensor(c, [n(0)], [n(6)], [n(1)])
        self.assertTrue(z3.is_false(z3.simplify(bad.valid)))

    def test_complete_counterexample_includes_invalid_rhs(self):
        g = self.load()
        case = g.build_case("concat_right", 1, "missing_offset")
        s = z3.Solver()
        s.add(*case.assertions)
        s.set(timeout=5000)
        self.assertEqual(s.check(), z3.sat)
        report = g.replay_case(case, s.model())
        self.assertEqual(report["result"], "pass")
        self.assertIn(report["violation"], ("rhs_invalid", "shape_mismatch", "element_mismatch", "rhs_access_invalid"))

    def test_pilot_entrypoint_has_no_external_toolchain_requirement(self):
        self.assertTrue(GEN.is_file(), "tensor generator implementation is missing")
        p = subprocess.run([sys.executable, str(GEN), "--list", "--mode", "pilot"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(p.stdout.strip().splitlines()), 5)



    def test_sat_mutants_replay_using_only_concrete_parameter_and_uf_reads(self):
        g = self.load()
        witnesses = [
            ("pad_restore", "unit_stride",
             {"extent_0": 3, "low_0": 1, "high_0": 1, "interior_0": 1,
              "padding_value": -9, "index_0": 0}),
            ("reverse_slice", "naive_mirror",
             {"extent_0": 7, "start_0": 1, "end_0": 7, "stride_0": 2, "index_0": 0}),
            ("concat_right", "missing_offset",
             {"extent_0": 3, "second_extent_0": 5, "start_0": 3,
              "end_0": 5, "stride_0": 1, "index_0": 0}),
            ("nested_slice", "dropped_inner_stride",
             {"extent_0": 20, "inner_start_0": 1, "inner_end_0": 18, "inner_stride_0": 3,
              "outer_start_0": 1, "outer_end_0": 5, "outer_stride_0": 2, "index_0": 0}),
        ]
        for rule, fault, values in witnesses:
            with self.subTest(rule=rule):
                case = g.build_case(rule, 1, fault)
                solver = z3.Solver()
                solver.set(timeout=5000)
                solver.add(*case.assertions)
                solver.add(*(z3.Int(k) == v for k, v in values.items()))
                self.assertEqual(solver.check(), z3.sat)
                model = solver.model()
                original_eval = model.eval
                def concrete_reads_only(expr, *args, **kwargs):
                    permitted = (z3.is_app(expr) and expr.decl().kind() == z3.Z3_OP_UNINTERPRETED
                                 and all(z3.is_int_value(c) for c in expr.children()))
                    self.assertTrue(permitted,
                                    "replay evaluated a symbolic arithmetic/validity/assertion expression")
                    return original_eval(expr, *args, **kwargs)
                model.eval = concrete_reads_only
                report = g.replay_case(case, model)
                self.assertEqual(report["result"], "pass", report)
                self.assertEqual(report["level"], "independent_python_integer_tensor_replay")
                self.assertEqual(report["native_application_replay"], "not_run")
                self.assertIn(report["violation"],
                              ("rhs_invalid", "shape_mismatch", "element_mismatch", "rhs_access_invalid"))

    def test_pure_replay_matches_hand_computed_reverse_coordinates(self):
        g = self.load()
        path = CODE / "concrete_replay.py"
        self.assertTrue(path.is_file(), "independent concrete tensor interpreter is missing")
        spec = importlib.util.spec_from_file_location("concrete_tensor_test", path)
        concrete = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = concrete
        spec.loader.exec_module(concrete)
        parameters = {"extent_0": 7, "start_0": 1, "end_0": 7,
                      "stride_0": 2, "index_0": 0}
        report = concrete.replay("reverse_slice", 1, "naive_mirror", parameters,
                                 lambda name, coordinates: 10 * (coordinates[0] + 1))
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["lhs_value"], 60)
        self.assertEqual(report["rhs_value"], 50)
        self.assertEqual(report["uf_reads"][0]["coordinates"], [5])
        self.assertEqual(report["uf_reads"][1]["coordinates"], [4])

    def test_pure_replay_rejects_non_witness_and_checks_correct_rewrite(self):
        g = self.load()
        path = CODE / "concrete_replay.py"
        self.assertTrue(path.is_file(), "independent concrete tensor interpreter is missing")
        spec = importlib.util.spec_from_file_location("concrete_tensor_test", path)
        concrete = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = concrete
        spec.loader.exec_module(concrete)
        params = {"extent_0": 3, "low_0": 1, "high_0": 1, "interior_0": 1,
                  "padding_value": -9, "index_0": 1}
        report = concrete.replay("pad_restore", 1, None, params,
                                 lambda name, coordinates: [4, 7, 11][coordinates[0]])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["reason"], "no concrete rewrite violation")
        params["extent_0"] = 0
        report = concrete.replay("pad_restore", 1, None, params,
                                 lambda name, coordinates: 1)
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["reason"], "input or rewrite precondition is false")



    def test_pure_replay_keeps_arbitrary_precision_without_materializing_tensors(self):
        path = CODE / "concrete_replay.py"
        self.assertTrue(path.is_file())
        spec = importlib.util.spec_from_file_location("concrete_tensor_test", path)
        concrete = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(concrete)
        enormous = 10 ** 40
        params = {"extent_0": 2, "extent_1": enormous,
                  "start_0": 0, "end_0": 2, "stride_0": 1,
                  "start_1": 0, "end_1": enormous, "stride_1": 1,
                  "index_0": 1, "index_1": enormous - 1}
        try:
            report = concrete.replay("slice_broadcast", 2, None, params,
                                     lambda name, coordinates: coordinates[0] + 7)
        except OverflowError:
            self.fail("unbounded SMT integers overflowed a Python container-size operation")
        self.assertEqual(report["reason"], "no concrete rewrite violation")
        self.assertEqual(report["lhs_shape"], [2, enormous])
        self.assertEqual(report["rhs_shape"], [2, enormous])


if __name__ == "__main__":
    unittest.main()

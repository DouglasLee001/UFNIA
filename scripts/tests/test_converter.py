"""Black-box checks for UFNIA conversion, with an independent native decoder.

Only the test oracle invokes Solver.check(); the converter is a preprocessor.
The native decoder deliberately does not import any production converter code.
"""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import z3


PROJECT = Path(__file__).resolve().parents[2]
CONVERTER = PROJECT / "scripts" / "convert_ufnia.py"
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
INT_NAME = re.compile(r"i_[0-9]+\Z")
BOOL_NAME = re.compile(r"b_[0-9]+\Z")
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def read_expression(tokens):
    """Read exactly one whitespace-tokenized S-expression."""
    position = 0

    def read():
        nonlocal position
        if position >= len(tokens):
            raise AssertionError("unfinished native expression")
        token = tokens[position]
        position += 1
        if token == "(":
            result = []
            while position < len(tokens) and tokens[position] != ")":
                result.append(read())
            if position == len(tokens):
                raise AssertionError("missing native closing parenthesis")
            position += 1
            return result
        if token == ")" or "(" in token or ")" in token:
            raise AssertionError("native parentheses must be separate tokens")
        return token

    result = read()
    if position != len(tokens):
        raise AssertionError("extra tokens after native expression")
    return result


def validate_monomial(term):
    """Return variable factors, checking the consumer's int64/binary grammar."""
    if isinstance(term, str):
        if INT_NAME.fullmatch(term):
            return 1
        raise AssertionError(f"expected native variable, got: {term}")
    if len(term) != 3 or term[0] != "*":
        raise AssertionError(f"native monomial is not a binary product: {term}")
    first, second = term[1:]
    if isinstance(first, str) and INTEGER.fullmatch(first):
        if not INT64_MIN <= int(first) <= INT64_MAX:
            raise AssertionError("native coefficient is outside signed int64")
        if isinstance(second, str) and INT_NAME.fullmatch(second):
            return 1
        if (isinstance(second, list) and len(second) == 3 and second[0] == "*"
                and all(isinstance(item, str) and INT_NAME.fullmatch(item)
                        for item in second[1:])):
            return 2
        raise AssertionError(f"unsupported coefficient-times-monomial form: {term}")
    if all(isinstance(item, str) and INT_NAME.fullmatch(item) for item in term[1:]):
        return 2
    raise AssertionError(f"native product is not over two variables: {term}")


def validate_atom(atom):
    if isinstance(atom, str):
        if not BOOL_NAME.fullmatch(atom):
            raise AssertionError(f"invalid native Boolean symbol: {atom}")
        return
    if (len(atom) != 3 or atom[0] not in ("=", "<=")
            or not isinstance(atom[2], str) or not INTEGER.fullmatch(atom[2])):
        raise AssertionError(f"expected normalized polynomial atom, got {atom}")
    if not INT64_MIN <= int(atom[2]) <= INT64_MAX:
        raise AssertionError("native right-hand constant is outside signed int64")
    polynomial = atom[1]
    if isinstance(polynomial, list) and polynomial and polynomial[0] == "+":
        if len(polynomial) < 2:
            raise AssertionError("empty native sum")
        for term in polynomial[1:]:
            validate_monomial(term)
    else:
        validate_monomial(polynomial)


def smt_expression(expression):
    if isinstance(expression, list):
        return "(" + " ".join(smt_expression(part) for part in expression) + ")"
    # SMT-LIB integer negatives are expressions; native negatives are tokens.
    if INTEGER.fullmatch(expression) and int(expression) < 0:
        return f"(- {abs(int(expression))})"
    return expression


def decode_native(text):
    """Decode the actual atom-table/CNF wire format and return a Z3 formula."""
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise AssertionError("native output must contain nonempty records")
    cursor = 0

    def record():
        nonlocal cursor
        if cursor >= len(lines):
            raise AssertionError("native output ended prematurely")
        line = lines[cursor]
        cursor += 1
        return line

    limit = int(record())
    if limit < 1:
        raise AssertionError("atom ID bound must be maximum ID plus one")
    atoms = []
    while True:
        line = record()
        if line == "0":
            break
        fields = line.split()
        if not fields or fields[0] != str(len(atoms) + 1):
            raise AssertionError("atom IDs must be continuous and positive")
        atom = read_expression(fields[1:])
        validate_atom(atom)
        atoms.append(atom)
    if limit != len(atoms) + 1:
        raise AssertionError("first line does not match maximum atom ID plus one")
    clause_count = int(record())
    if clause_count < 0:
        raise AssertionError("negative clause count")
    clauses = []
    for _ in range(clause_count):
        clause = read_expression(record().split())
        if not isinstance(clause, list) or not clause:
            raise AssertionError("empty clauses crash the SLS native parser")
        if any(not isinstance(item, str) or not INTEGER.fullmatch(item) for item in clause):
            raise AssertionError("clause contains a non-integer literal")
        literals = [int(item) for item in clause]
        if any(literal == 0 or abs(literal) >= limit for literal in literals):
            raise AssertionError("clause references an absent atom")
        clauses.append(literals)
    if record() != "0" or cursor != len(lines):
        raise AssertionError("expected only a trailing zero restoration count")

    names = set(re.findall(r"\b[ib]_[0-9]+\b", text))
    declarations = "\n".join(
        f"(declare-fun {name} () {'Int' if name.startswith('i_') else 'Bool'})"
        for name in sorted(names)
    )
    parsed_atoms = list(z3.parse_smt2_string(
        declarations + "\n" + "\n".join(
            f"(assert {smt_expression(atom)})" for atom in atoms
        )
    ))
    formula = z3.And(*(
        z3.Or(*(parsed_atoms[abs(literal) - 1] if literal > 0
                else z3.Not(parsed_atoms[abs(literal) - 1]) for literal in clause))
        for clause in clauses
    ))
    return formula, {"atom_count": len(atoms), "clause_count": len(clauses), "atoms": atoms}


class ConverterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ufnia-converter-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "input.smt2"
        self.output = self.root / "output.nia"
        self.conversion_number = 0

    def run_converter(self, text, *, source=None, output=None, extra=()):
        source = source or self.source
        output = output or self.output
        source.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(CONVERTER), str(source), "-o", str(output), *map(str, extra)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(source.read_text(encoding="utf-8"), text, "converter changed its input")
        return result

    def check_result(self, formula, expected):
        solver = z3.Solver()
        solver.set(timeout=3000)
        solver.add(formula)
        self.assertEqual(solver.check(), expected)

    def assert_conversion(self, text, expected, *, extra=()):
        self.conversion_number += 1
        self.output = self.root / f"converted-{self.conversion_number}.nia"
        result = self.run_converter(text, extra=extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.output.is_file(), result.stdout + result.stderr)
        formula, decoded = decode_native(self.output.read_text(encoding="utf-8"))
        metadata = json.loads(Path(str(self.output) + ".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["z3_version"], z3.get_version_string())
        self.assertEqual(metadata["atom_count"], decoded["atom_count"])
        self.assertEqual(metadata["clause_count"], decoded["clause_count"])
        self.check_result(z3.And(*z3.parse_smt2_string(text)), expected)
        self.check_result(formula, expected)
        return decoded

    def assert_rejection(self, text, *, source=None, output=None, extra=()):
        output = output or self.output
        result = self.run_converter(text, source=source, output=output, extra=extra)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.strip(), "rejection needs a useful diagnostic")
        self.assertFalse(output.exists(), "failure left a native output behind")
        self.assertFalse(Path(str(output) + ".json").exists(), "failure left metadata behind")
        return result

    def test_uf_congruence_makes_equal_arguments_unsatisfiable(self):
        self.assert_conversion("""
            (declare-fun f (Int) Int)
            (declare-const x Int) (declare-const y Int)
            (assert (<= x y)) (assert (<= y x))
            (assert (distinct (f x) (f y)))
            (check-sat)
        """, z3.unsat)

    def test_uf_can_differ_on_distinct_arguments(self):
        self.assert_conversion("""
            (declare-fun f (Int) Int)
            (declare-const x Int) (declare-const y Int)
            (assert (distinct x y)) (assert (distinct (f x) (f y)))
            (check-sat)
        """, z3.sat)

    def test_boolean_uf_preserves_congruence_and_independent_values(self):
        for equal, expected in ((True, z3.unsat), (False, z3.sat)):
            with self.subTest(equal=equal):
                relation = "(and (<= x y) (<= y x))" if equal else "(distinct x y)"
                self.assert_conversion(f"""
                    (declare-fun p (Int) Bool)
                    (declare-const x Int) (declare-const y Int)
                    (assert {relation}) (assert (p x)) (assert (not (p y)))
                    (check-sat)
                """, expected)

    def test_nested_uf_and_boolean_argument_are_supported(self):
        self.assert_conversion("""
            (declare-fun f (Int) Int) (declare-fun g (Bool Int) Int)
            (declare-fun p (Int) Bool) (declare-const x Int)
            (assert (distinct (f (f x)) (f x)))
            (assert (= (g (p x) (f x)) (+ (f (f x)) 1)))
            (check-sat)
        """, z3.sat)

    def test_arithmetic_ite_is_eliminated_without_losing_branches(self):
        for choose_square, expected in ((True, z3.sat), (False, z3.unsat)):
            with self.subTest(choose_square=choose_square):
                self.assert_conversion(f"""
                    (declare-const x Int) (declare-const p Bool)
                    (assert (> x 1)) (assert (< x 3))
                    (assert {'p' if choose_square else '(not p)'})
                    (assert (= (ite p (* x x) (+ x 3)) 4))
                    (check-sat)
                """, expected)

    def test_boolean_structure_and_distinct_are_encoded(self):
        self.assert_conversion("""
            (declare-const a Bool) (declare-const b Bool) (declare-const c Bool)
            (declare-const x Int) (declare-const y Int) (declare-const z Int)
            (assert (= c (xor a b))) (assert (=> c (or a b)))
            (assert (ite a c (not b))) (assert c)
            (assert (distinct x y z))
            (assert (or (= x y) (and (< x z) (<= z y))))
            (check-sat)
        """, z3.sat)

    def test_strict_integer_comparisons_shift_by_one(self):
        for assertion, expected in (("(< x 1)", z3.unsat), ("(< x 2)", z3.sat)):
            with self.subTest(assertion=assertion):
                self.assert_conversion(f"""
                    (declare-const x Int)
                    (assert (> x 0)) (assert {assertion})
                    (check-sat)
                """, expected)

    def test_negative_coefficients_and_numerals_are_preserved(self):
        self.assert_conversion("""
            (declare-const x Int) (declare-const y Int)
            (assert (< x (- 2))) (assert (> x (- 4)))
            (assert (<= (+ (* (- 3) x) y) (- 5)))
            (assert (>= y (- 14)))
            (check-sat)
        """, z3.sat)

    def test_negative_division_and_modulo_follow_integer_semantics(self):
        for denominator, quotient in ((3, -3), (-3, 3)):
            with self.subTest(denominator=denominator):
                divisor = str(denominator) if denominator > 0 else "(- 3)"
                expected_quotient = str(quotient) if quotient > 0 else "(- 3)"
                self.assert_conversion(f"""
                    (declare-const x Int)
                    (assert (> x (- 8))) (assert (< x (- 6)))
                    (assert (or (distinct (div x {divisor}) {expected_quotient})
                                (distinct (mod x {divisor}) 2)))
                    (check-sat)
                """, z3.unsat)

    def test_zero_division_and_modulo_are_not_forced_to_zero(self):
        self.assert_conversion("""
            (declare-const x Int)
            (assert (> x 1)) (assert (< x 3))
            (assert (= (div x 0) 7)) (assert (= (mod x 0) 9))
            (check-sat)
        """, z3.sat)

    def test_zero_division_and_modulo_preserve_equal_argument_consistency(self):
        for operation in ("div", "mod"):
            with self.subTest(operation=operation):
                self.assert_conversion(f"""
                    (declare-const x Int) (declare-const y Int)
                    (declare-const zero Int)
                    (assert (<= x y)) (assert (<= y x))
                    (assert (<= zero 0)) (assert (>= zero 0))
                    (assert (distinct ({operation} x zero) ({operation} y 0)))
                    (check-sat)
                """, z3.unsat)

    def test_high_degree_product_uses_binary_definitions(self):
        decoded = self.assert_conversion("""
            (declare-const a Int) (declare-const b Int) (declare-const c Int)
            (declare-const d Int) (declare-const e Int)
            (assert (> a 1)) (assert (< a 6))
            (assert (> b 1)) (assert (< b 6))
            (assert (> c 1)) (assert (< c 6))
            (assert (> d 1)) (assert (< d 6))
            (assert (> e 1)) (assert (< e 6))
            (assert (= (* a b c d e) 120))
            (check-sat)
        """, z3.sat)
        self.assertGreater(decoded["atom_count"], 0)
        self.assertTrue(any("*" in str(atom) for atom in decoded["atoms"]))

    def test_constant_true_is_representable(self):
        self.assert_conversion("(assert true)\n(check-sat)\n", z3.sat)

    def test_constant_false_avoids_empty_native_clauses(self):
        decoded = self.assert_conversion("(assert false)\n(check-sat)\n", z3.unsat)
        self.assertGreater(decoded["atom_count"], 0)
        self.assertGreater(decoded["clause_count"], 0)

    def test_macros_let_quoting_comments_and_commandlike_strings(self):
        self.assert_conversion('''
            (set-info :source "text (check-sat) (push 1) and ""quoted"" text")
            ; (pop 1) is not a command here.
            (declare-fun |f (strange name)| (Int) Int)
            (declare-const |x value| Int)
            (define-fun bump ((argument Int)) Int (+ argument 1))
            (assert (let ((saved (|f (strange name)| |x value|)))
                (> (bump saved) saved)))
            (check-sat)
        ''', z3.sat)

    def test_rejects_int64_overflow_after_preprocessing(self):
        self.assert_rejection(f"""
            (declare-const x Int) (declare-const y Int)
            (assert (> x {2**256})) (assert (< x y)) (assert (> y 0))
            (check-sat)
        """)

    def test_rejects_quantifiers_and_non_integer_boolean_sorts(self):
        cases = {
            "quantifier": "(assert (forall ((x Int)) (= x x)))",
            "real": "(declare-const x Real) (assert (> x 0.0))",
            "bitvector": "(declare-const x (_ BitVec 8)) (assert (= x #x01))",
            "array": "(declare-const x (Array Int Int)) (assert (= (select x 0) 1))",
            "datatype": "(declare-datatypes ((D 0)) (((mkD (field Int))))) "
                        "(declare-const x D) (assert (= (field x) 1))",
            "power": "(declare-const x Int) (assert (> (^ x 3) 2))",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                self.assert_rejection(body + "\n(check-sat)\n")

    def test_rejects_incremental_and_ambiguous_command_streams(self):
        cases = [
            "(check-sat)\n(check-sat)\n",
            "(push 1)\n(assert true)\n(check-sat)\n",
            "(assert true)\n(pop 1)\n(check-sat)\n",
            "(assert false)\n(reset)\n(check-sat)\n",
            "(assert false)\n(reset-assertions)\n(check-sat)\n",
            "(check-sat-assuming ())\n",
            "(assert true)\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assert_rejection(text)

    def test_rejects_assumptions_on_check_sat_extension(self):
        for text in (
            '(check-sat false)\n',
            '(declare-const p Bool) (assert (not p)) (check-sat p)\n',
        ):
            with self.subTest(text=text):
                self.assert_rejection(text)

    def test_rejects_int64_min_rhs_that_native_reader_negates_in_int64(self):
        self.assert_rejection('''
            (declare-const x Int) (declare-const y Int)
            (assert (= (+ (* x x) (* y y)) (- 9223372036854775808)))
            (check-sat)
        ''')

    def test_rejects_malformed_input_and_invalid_resource_options(self):
        self.assert_rejection("(declare-const x Int)\n(assert (= x ))\n(check-sat)\n")
        for option, value in (("--timeout", "0"), ("--timeout", "-1"),
                              ("--ackermann-limit", "-1")):
            with self.subTest(option=option, value=value):
                self.assert_rejection("(assert true)\n(check-sat)\n", extra=(option, value))
        self.assert_rejection("""
            (declare-fun f (Int) Int)
            (declare-const x Int) (declare-const y Int)
            (assert (or (= (f x) 0) (= (f y) 1)))
            (assert (< x y))
            (check-sat)
        """, extra=("--ackermann-limit", "0"))

    def test_refuses_input_output_aliases_before_writing(self):
        text = "(assert true)\n(check-sat)\n"
        self.source.write_text(text, encoding="utf-8")
        aliases = [self.source, self.root / "symbolic.nia", self.root / "hard.nia"]
        aliases[1].symlink_to(self.source)
        aliases[2].hardlink_to(self.source)
        for output in aliases:
            with self.subTest(output=output):
                result = self.run_converter(text, output=output)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(output.read_text(), text)
                self.assertFalse(Path(str(output) + ".json").exists())
        metadata_source = self.root / "metadata.nia.json"
        result = self.run_converter(text, source=metadata_source, output=self.root / "metadata.nia")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.root / "metadata.nia").exists())
        self.assertEqual(metadata_source.read_text(), text)
        result = self.run_converter(text, extra=("--emit-smt2", self.source))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.output.exists())

    def test_refuses_colliding_artifact_paths_and_preserves_existing_files_on_failure(self):
        text = "(assert true)\n(check-sat)\n"
        for emitted in (self.output, Path(str(self.output) + ".json")):
            with self.subTest(emitted=emitted):
                self.assert_rejection(text, extra=("--emit-smt2", emitted))
        emitted = self.root / "normalized.smt2"
        self.output.write_text("existing native\n")
        metadata = Path(str(self.output) + ".json")
        metadata.write_text("existing metadata\n")
        emitted.write_text("existing SMT-LIB\n")
        result = self.run_converter(
            "(assert (forall ((x Int)) (= x x)))\n(check-sat)\n",
            extra=("--emit-smt2", emitted),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.output.read_text(), "existing native\n")
        self.assertEqual(metadata.read_text(), "existing metadata\n")
        self.assertEqual(emitted.read_text(), "existing SMT-LIB\n")
        result = self.run_converter(text, extra=("--emit-smt2", emitted))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.output.read_text(), "existing native\n")
        self.assertEqual(metadata.read_text(), "existing metadata\n")
        self.assertEqual(emitted.read_text(), "existing SMT-LIB\n")

    def test_optional_normalized_smt2_is_parseable_and_equisatisfiable(self):
        emitted = self.root / "normalized.smt2"
        self.assert_conversion("""
            (declare-fun f (Int) Int) (declare-const x Int) (declare-const y Int)
            (assert (> x 0)) (assert (< x y))
            (assert (distinct (f x) (f y)))
            (check-sat)
        """, z3.sat, extra=("--emit-smt2", emitted, "--timeout", "30"))
        self.assertTrue(emitted.is_file())
        self.check_result(z3.And(*z3.parse_smt2_file(str(emitted))), z3.sat)


if __name__ == "__main__":
    unittest.main()

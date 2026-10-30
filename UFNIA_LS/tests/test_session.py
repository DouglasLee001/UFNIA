import io
import unittest

import z3

try:
    from UFNIA_LS.session import SessionReader, SessionError
except ImportError:
    SessionReader = None
    SessionError = ValueError


class SessionTests(unittest.TestCase):
    def read(self, text, **kwargs):
        self.assertIsNotNone(SessionReader, 'incremental session frontend is missing')
        reader = SessionReader(io.StringIO(text), **kwargs)
        return reader, list(reader)

    def test_incremental_snapshots_and_local_declarations(self):
        reader, qs = self.read('''
            (declare-const x Int) (assert (> x 0)) (check-sat)
            (push 1) (declare-const y Int) (assert (= y x)) (check-sat)
            (pop 1) (assert (< x 8)) (check-sat)
        ''')
        self.assertEqual([q.index for q in qs], [1, 2, 3])
        self.assertEqual([len(q.assertions) for q in qs], [1, 2, 2])
        self.assertEqual([str(a) for a in qs[0].assertions], ['x > 0'])
        self.assertEqual(reader.stats['checks'], 3)
        self.assertEqual(reader.stats['maxdepth'], 1)
        self.assertEqual(reader.stats['assertions'], 3)
        with self.assertRaisesRegex(SessionError, 'y|unknown'):
            self.read('(push 1)(declare-const y Int)(pop 1)(assert (= y 0))(check-sat)')

    def test_macros_and_let_survive_parser_batches(self):
        _, qs = self.read('''
            (declare-const x Int)
            (define-fun square ((n Int)) Int (* n n))
            (assert (= (let ((v (square x))) (+ v 1)) 5))
            (check-sat) (assert (> x 0)) (check-sat)
        ''', batch_bytes=30)
        self.assertEqual(len(qs), 2)
        self.assertIn('x*x', str(qs[0].assertions[0]))
        self.assertEqual(len(qs[1].assertions), 2)

    def test_quotes_comments_and_source_are_not_commands(self):
        _, qs = self.read('''
            ; (check-sat) (push 99)
            (set-info :source |source (push 88)\n(check-sat)|)
            (set-info :notes "text ""(check-sat)"" ; ()")
            (declare-const |x();| Int)
            (assert (> |x();| 0)) ; ) )
            (check-sat)
        ''', chunk_size=3)
        self.assertEqual(len(qs), 1)
        self.assertEqual(len(qs[0].assertions), 1)

    def test_uninterpreted_sort_declarations_and_scoped_sort_aliases(self):
        _, qs = self.read('''
            (set-logic QF_UFNIA)(declare-sort S1 0)
            (declare-fun f1 () S1)(declare-fun f2 () S1)
            (assert (not (= f1 f2)))(check-sat)
            (push 1)(define-sort Number () Int)
            (declare-const n Number)(assert (> n 0))(check-sat)(pop 1)
        ''')
        self.assertEqual([q.assertion_count for q in qs], [1, 2])
        self.assertEqual(str(qs[0].assertions[0].arg(0).arg(0).sort()), 'S1')
        with self.assertRaises(SessionError):
            self.read('(push 1)(declare-sort Local 0)(pop 1)(declare-const x Local)(check-sat)')

    def test_assumptions_are_temporary_and_parsed(self):
        _, qs = self.read('''
            (declare-const p Bool) (declare-const q Bool)
            (assert (or p q))
            (check-sat-assuming (p (not q))) (check-sat)
            (check-sat-assuming ())
        ''')
        self.assertEqual([len(q.assertions) for q in qs], [3, 1, 1])
        self.assertEqual(qs[0].command, 'check-sat-assuming')
        self.assertEqual([str(a) for a in qs[0].assumptions], ['p', 'Not(q)'])
        with self.assertRaises(SessionError):
            self.read('(declare-const x Int)(check-sat-assuming (x))')

    def test_assumption_annotations_do_not_leak_parser_bindings(self):
        with self.assertRaises(SessionError):
            self.read('''
                (declare-const p Bool)
                (check-sat-assuming ((! p :named assumption_only)))
                (check-sat-assuming (assumption_only))
            ''')

    def test_reset_assertions_pops_scopes_and_reset_clears_symbols(self):
        _, qs = self.read('''
            (declare-const x Int) (assert (> x 0))
            (push 1) (declare-const y Int) (assert (= y 0))
            (reset-assertions) (assert (= x 2)) (check-sat)
            (reset) (declare-const x Bool) (assert x) (check-sat)
        ''')
        self.assertEqual([len(q.assertions) for q in qs], [1, 1])
        self.assertTrue(z3.is_int(qs[0].assertions[0].arg(0)))
        self.assertTrue(z3.is_bool(qs[1].assertions[0]))
        with self.assertRaises(SessionError):
            self.read('(push 1)(declare-const y Int)(reset-assertions)(assert (= y 0))(check-sat)')

    def test_global_declarations_survive_pop_and_reset_assertions(self):
        _, qs = self.read('''
            (set-option :global-declarations true)
            (push 1) (declare-const y Int) (define-fun f ((x Int)) Int (+ x y))
            (assert (= y 0)) (pop 1)
            (assert (= (f 1) 3)) (check-sat)
            (push 1) (declare-const z Int) (reset-assertions)
            (assert (= z y)) (check-sat)
        ''')
        self.assertEqual([len(q.assertions) for q in qs], [1, 1])

    def test_new_assertion_callback_does_not_repeat_shared_context(self):
        batches = []
        reader, qs = self.read('''
            (declare-const p Bool)(assert p)(check-sat)(check-sat)
            (check-sat-assuming ((not p)))(check-sat)
        ''', on_assertions=lambda exprs: batches.append(tuple(exprs)))
        self.assertEqual([len(b) for b in batches], [1, 1])
        self.assertEqual(reader.stats['checks'], 4)
        self.assertEqual(len(qs[-1].assertions), 1)

    def test_parse_only_snapshots_count_assertions_without_materializing_them(self):
        seen = []
        reader, qs = self.read('''
            (declare-const p Bool)(assert p)(check-sat)
            (push 1)(assert (not p))(check-sat-assuming (p))
            (pop 1)(check-sat)(reset-assertions)(check-sat)
        ''', snapshots=False, on_assertions=lambda batch: seen.extend(batch))
        self.assertEqual([q.index for q in qs], [1, 2, 3, 4])
        self.assertEqual([q.assertion_count for q in qs], [1, 3, 1, 0])
        self.assertTrue(all(q.assertions == () for q in qs))
        self.assertEqual(len(qs[1].assumptions), 1)
        self.assertEqual(len(seen), 3)
        self.assertEqual(reader.stats['checks'], 4)

    def test_assertion_only_scopes_do_not_require_parser_scope_roundtrips(self):
        reader, qs = self.read(
            '(declare-const p Bool)' +
            '(push 1)(assert p)(check-sat)(pop 1)' * 20,
            snapshots=False,
        )
        self.assertEqual([q.assertion_count for q in qs], [1] * 20)
        self.assertLessEqual(reader.stats['parse_calls'], 22)

    def test_named_assertions_have_local_symbol_scope(self):
        _, qs = self.read('''
            (declare-const p Bool)(push 2)
            (assert (! p :named local_name))(check-sat)
            (check-sat-assuming (local_name))(pop 2)(check-sat)
        ''')
        self.assertEqual([q.assertion_count for q in qs], [1, 2, 0])
        with self.assertRaises(SessionError):
            self.read('''
                (declare-const p Bool)(push 1)(assert (! p :named n))
                (pop 1)(check-sat-assuming (n))
            ''')

    def test_stream_never_reads_entire_input(self):
        class BoundedStream(io.StringIO):
            def read(self, size=-1):
                if size < 0 or size > 64:
                    raise AssertionError('unbounded read')
                return super().read(size)
        self.assertIsNotNone(SessionReader, 'incremental session frontend is missing')
        reader = SessionReader(BoundedStream('(declare-const x Int)(assert (> x 0))(check-sat)'), chunk_size=7)
        self.assertEqual(len(list(reader)), 1)

    def test_malformed_commands_are_rejected(self):
        for text in ['(assert true', ')', 'garbage(check-sat)', '(push -1)(check-sat)',
                     '(pop 1)(check-sat)', '(check-sat extra)', '(check-sat-assuming p)',
                     '(check-sat)(exit)(check-sat)', '(set-info :source |unterminated)',
                     '(set-info :notes "unterminated)']:
            with self.subTest(text=text), self.assertRaises(SessionError):
                self.read(text, chunk_size=3)


if __name__ == '__main__':
    unittest.main()

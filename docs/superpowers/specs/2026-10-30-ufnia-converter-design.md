# UFNIA to NIA_LS converter

Approved in conversation on 2026-10-30: implement a Python + Z3 frontend, a single-file converter, a compiled SLS executable, and real benchmark input validation. Unsupported constructs must fail explicitly.

## Interface and scope

`python3 scripts/convert_ufnia.py INPUT.smt2 -o OUTPUT.nia` reads one non-incremental SMT-LIB instance. It writes the atom-table/CNF format consumed by the supplied SLS. The compiled executable is `build/nia_ls/nia_ls`: the source directory is root-owned, so the original Makefile is used with VPATH in a writable build directory. The converter also writes JSON metadata and an optional normalized SMT-LIB dump. Input benchmarks remain unchanged. Batch experiments and reconstruction of original UF models are outside this change.

Use installed Z3Py 4.16.0 as a preprocessing library, recording its actual version. This is distinct from the project's Z3 5.1.0 command-line competitor. The converter never calls a complete SMT solver. Tests may use Z3 solving to check equisatisfiability.

## Translation

Parse using Z3's SMT-LIB parser so macros, let expressions, quoting and UF declarations retain their meaning. Require a single check-sat context without push/pop/reset/check-sat-assuming. Reject quantifiers and sorts other than Int/Bool before preprocessing.

Apply bounded simplification, propagation, equation elimination and term-ITE elimination. Purify integer div/mod with `complete=False`, which preserves ground-function consistency through congruence axioms. Apply Ackermann elimination with an explicit lemma cap; detect any residual nonconstant UF, div/mod, arithmetic ITE or unsupported operation rather than trusting tactic success. Expand distinct and Tseitin-encode Boolean structure. Use only precise goals and never drop branches.

Emit renamed Int/Bool symbols, continuous positive atom IDs, normalized polynomial equalities and non-strict inequalities, signed CNF references and a trailing zero restoration count. Strict integer comparisons are shifted by one. Negative numbers and all parentheses use the parser's exact token spacing. Lift long products into binary multiplication definitions rather than relying on unsupported high-degree local-search moves. Represent constant false with contradictory bounds, because an empty clause crashes the existing SLS parser.

Validate emitted constants and coefficients against signed int64: the current SLS uses atoll even though some internal arithmetic is int128. Also reject an RHS of INT64_MIN because the native parser negates it in int64 before assignment to int128. Certora inputs often exceed the supported range; report an error without emitting a usable-looking partial output. This change does not add arbitrary-precision arithmetic to SLS. Conversion success means an equisatisfiable native input was produced; it does not guarantee that finite-precision SLS finds or validates a model of the original SMT input.

## Validation

Independent native-format decoding and Z3 checks compare SAT/UNSAT on UF congruence, Boolean UF, nested functions, term ITE, negative/zero div/mod, strict comparisons, polynomial products and true/false. CLI tests cover malformed SMT-LIB, unsupported input, output path safety and resource failure without partial results. Compile the actual SLS and run generated files using `--strategy=corewarm2 --initialize=off --restart-repair=off` with an outer timeout; verify the search-entry diagnostic. Real benchmark smoke tests retain family paths and record conversion/parse outcomes separately from SAT results.

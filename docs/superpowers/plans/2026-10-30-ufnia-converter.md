# UFNIA Converter Implementation Plan

> **For agentic workers:** Use executing-plans for the implementation and independent agents for build and review. The user has already approved direct execution in this session.

**Goal:** Translate a QF_UFNIA SMT-LIB file into input that the supplied independent NIA_LS executable can consume.

**Architecture:** Z3Py parses and preprocesses a single non-incremental goal; a native serializer normalizes arithmetic atoms, renames symbols and writes atom-table/CNF input. A separate CLI enforces input/output safety and reports metadata. Tests decode the native format independently and compare semantics; actual SLS smoke tests verify the consumer boundary.

**Tech Stack:** Python 3, installed z3-solver 4.16, unittest, existing C++ NIA_LS with Z3/GMP.

---

### Task 1: Establish the consumer and failing tests

- [x] Compile with the original Makefile and VPATH in `build/nia_ls` (the source directory is root-owned); preserve the command and complete build log in `result/converter_validation/`.
- [x] Create `scripts/tests/test_converter.py`. Invoke `python3 scripts/convert_ufnia.py INPUT -o OUTPUT` in a temporary directory and assert successful conversion, then independently decode atoms and clauses into Z3 formulas. Check known SAT and UNSAT UF-congruence cases, div/mod including denominator zero, polynomial/ITE cases, true and false, and failures without output files.
- [x] Run `python3 -m unittest discover -s scripts/tests -p test_converter.py -v` before implementation and record the missing-converter failures.

### Task 2: Implement the frontend and native serializer

- [x] Create `scripts/ufnia_converter.py`: bounded precise Z3 tactics, strict AST/sort checks, atom normalization and safe native serialization. Expose `convert_text(text, timeout_ms=30000, ackermann_limit=100000)`, returning native text and conversion metadata, for CLI and tests.
- [x] Normalize `<`, `>`, `>=` over integers to `<=`, with negative CNF references for logical negation. Format products as `( * coefficient ( * i_0 i_1 ) )`; split larger products into fresh binary definitions. Preserve all integer div/mod and UF congruence semantics through preprocessing.
- [x] Create `scripts/convert_ufnia.py` with INPUT, required `-o`, optional `--emit-smt2`, `--timeout`, and `--ackermann-limit`. Validate all paths before writes, refuse source/output aliases, write through temporary files, and report concise failures with nonzero exit status. Record Z3 version and counts in OUTPUT.json.
- [x] Run the focused tests; fix only demonstrated semantic or parsing errors. Add reproductions before any further fixes.

### Task 3: Verify real input and document commands

- [x] Run generated SAT, false and nonlinear native inputs through the actual binary using `timeout 10s build/nia_ls/nia_ls OUTPUT --strategy=corewarm2 --initialize=off --restart-repair=off --native-seconds=1` and confirm `search_result` is present.
- [x] Convert selected original SAT benchmarks from multiple families with per-file resource caps, preserve paths under `result/converter_validation/`, and record parse/search results. Include a Certora out-of-range rejection and verify its output is absent.
- [x] Document the two-command conversion/search interface and dependencies in `scripts/CONVERTER.md`; include precision limits, source immutability, the independent-search options, metadata and optional SMT-LIB dump.
- [x] Request an independent semantic/code review; resolve material findings and rerun affected checks. Run the existing runner tests plus focused converter tests before completion.

No experimental solver runs are added to existing `results/*/latest`; no benchmark files are changed. No commits or branch changes are required for delivery.

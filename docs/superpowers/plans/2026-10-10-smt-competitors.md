# SMT Competitors Implementation Plan

> **For agentic workers:** Use independent solver build tasks in the current session, with root verification.

**Goal:** Find the latest official stable releases of Yices2, cvc5, SMTInterpol and Z3, compile them under competitors, and verify QF_UFNIA capability.

**Architecture:** Keep every solver and local dependencies isolated in competitors/<solver>. Preserve sources, release metadata, source hashes, build logs, and local executable entry points. Run common SMT-LIB cases and document any unsupported logic honestly.

**Tech Stack:** Ubuntu 24.04 x86_64, GCC, CMake/Ninja, Autotools, Java (local JDK if required), SMT-LIB 2.

- [x] Check local rules and toolchain, official latest non-prerelease release metadata and supported logics.
- [x] Build Yices2 with MCSAT and its required dependencies in competitors/yices2; expose bin/yices-smt2.
- [x] Build cvc5 release and arithmetic dependencies in competitors/cvc5; expose bin/cvc5.
- [x] Build Z3 release in competitors/z3; expose bin/z3.
- [x] Pin SMTInterpol official downloadable release to its source commit, build its executable JAR in competitors/smtinterpol; expose bin/smtinterpol.
- [x] Verify each version and common QF_UFNIA sat, nonlinear unsat and UF-congruence unsat cases with a timeout; preserve outputs. Verify a linear control case for SMTInterpol if nonlinear logic is unsupported.
- [x] Write competitors/README.md with versions, dates, sources, invocations, support limits, and validation results; update this checklist from evidence.

Commands for common verification: `competitors/z3/bin/z3 competitors/tests/qf_ufnia_sat.smt2`, `competitors/cvc5/bin/cvc5 competitors/tests/qf_ufnia_sat.smt2`, `competitors/yices2/bin/yices-smt2 competitors/tests/qf_ufnia_sat.smt2`, `competitors/smtinterpol/bin/smtinterpol competitors/tests/qf_ufnia_sat.smt2`. Repeat for unsat and UF congruence cases, recording return codes and full output.

Build tasks run with at most four compiler jobs each to limit memory pressure. No system-wide installation or source modifications unless required for toolchain compatibility; document any such change.

Verification completed: 4 versions and source checksums verified; 24 shared case invocations returned exit 0; all 18 Yices2/cvc5/Z3 results match logical expectations; SMTInterpol returned unknown on 4 nonlinear cases, unsat on the UF contradiction and sat on the linear control. Results are recorded in competitors/verification/smoke-results.json.

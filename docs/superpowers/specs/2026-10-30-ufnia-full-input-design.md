# Full QF_UFNIA input support

The user has explicitly replaced the earlier reject-unsupported scope with a requirement to accept every instance in `/data/shared/lbh/QF_UFNIA/non-incremental/QF_UFNIA`. That directory, rather than the briefly mentioned incremental QF_NIA library, is the acceptance corpus: 806 files across seven families. The reference NIA_LS is not an immutable backend constraint.

Create a writable `UFNIA_LS` implementation using the reference's existing GMP search/preprocessing components. Extend candidate generation to exact quadratic arithmetic and retain higher-degree fallback. Z3 supplies parsing and semantics-preserving preprocessing only; actual solving is stochastic local search with GMP, and every SAT candidate is checked exactly.

The frontend must remove UF applications with congruence constraints, preserve zero-divisor semantics, encode uninterpreted carrier sorts used only by equality into integer representatives, eliminate term ITE, and emit arbitrary-precision QF_NIA for the new core. The old int64 reader remains available only as an explicit legacy target. It cannot constrain the new solver's input range.

Provide `UFNIA_LS/run.sh INPUT.smt2 --parse-only` to validate the entire input through preprocessing and the actual compiled GMP consumer, and omit `--parse-only` for real local search. Retain an optional intermediate output and conversion statistics. Validate the full 806-file corpus, recording one row per original relative path and verifying that sources are unchanged. Parse coverage and SAT performance must be reported separately. Test original UF congruence, Boolean functions, opaque sorts, division by zero, huge integer products and exact model checking before claiming completion.

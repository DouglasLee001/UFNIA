# Current retained mature implementation

2026-11-12: the working tree retains single-root affine views and removes the optional footprint cache. Each affine patch uses the ordinary footprint construction and numeric preflight. The cache table, retained owners, invalidation, environment switch and report fields have been removed. UFNIA_AFFINE_VIEWS=0 still disables affine views.

Use --backend mature --uf-observers --interaction joint --scoring paws --seed 0 through the Python driver. The Makefile uses the copied static GMP in build/ufnia_ls/deps/gmp when present and otherwise uses system GMP.

The completed full-corpus ablation belongs to the preserved old binary: 419 SAT with affine/cache, 419 with affine only, 413 with both disabled. The historical 70/191 result belongs to its original frozen snapshot. No new full-corpus performance score is claimed for this removal.

The removal build (f91cf2b7...) passed all 71 tests across the eight mature regression modules on node2; the node3 launcher passed syntax and dry-run checks.

The following describes the retained reference-kernel behavior and historical interface.

# Mature GMP backend, first slice

Build with `make -C UFNIA_LS mature`. The executable is
`build/ufnia_ls/ufnia_mature INPUT.native --seconds N --seed N --model-output PATH`.
`--parse-only` follows the same reader and term/literal/CNF construction path and
prints a JSON summary. Solve stdout is `sat` or `unknown`; the last stderr line
is JSON with `backend=mature_mpz`, `backend_version=mature-mpz-v1`, and `validated`.
Models map native symbol names to exact decimal strings or Boolean JSON values.

`nia_ls.cpp`, `nia_initialize.cpp`, `nia_ls.h`, `nia_Array.h`, and
`nia_novelty_score.h` originate in this repository's `NIA_LS` mature kernel.
The first slice selects its plain strategy, preserving the term/literal
incidence structure, CSR make/break scoring, shared-term coefficient caches,
signed critical candidates, BMS selection, reverse/directional tabu, PAWS,
random walk, restart, and Boolean/NIA phase transitions. Coefficients,
assignments, residuals, term products, powers, division and quadratic root
boundaries use GMP integers. Floating point is confined to heuristic distance,
probability and elapsed time. Bounds and finite pre-values have explicit
presence flags; arbitrary machine-sized numerical clipping is absent.

The new native adapter deliberately omits internal equality elimination and
Boolean resolution. Original named variables remain available for export.
It derives only necessary unit linear bounds and exact exponent-one finite
pre-values, while retaining all original clauses. A separate immutable parsed
polynomial/CNF table is evaluated exactly before accepting SAT. An unsuccessful
search returns unknown and emits no model; it never claims UNSAT.

Auxiliary native-child dispatch, linear initialization, restart repair solvers,
external/warm models and optional legacy joint operators are disabled. The
binary does not link Z3 or invoke any other solver. Original `NIA_LS` files are
unchanged.

Differential verification is retained and can be built independently:

```
make -C UFNIA_LS/mature TARGET=/tmp/ufnia_mature_verify \
  CXXFLAGS='-O1 -g -std=c++17 -DNIA_SCORING_VERIFY -DNIA_CANDIDATE_VERIFY -DNIA_FAST_MOVE_VERIFY'
MATURE_BINARY=/tmp/ufnia_mature_verify python3 -m unittest discover \
  -s tests -p test_mature_core.py -v
```

The dedicated tests cover >128-bit coefficients/roots and actual critical
moves, negative/strict/negated inequalities, the former int32 sentinel value,
multilinear products and higher powers, zero-factor activation, Boolean aliases,
constant/empty CNF, malformed numbers, and a guarded platform that actually
executes both phases, phase switches and PAWS updates.

## Explicit observed UF mode (v2)

`--uf-observers --interaction none|soft|joint --scoring count|paws` enables
`mature-mpz-v2`. The native file must then end with `UFNIA_OBSERVERS 1 N`, followed
by N application lines `FID ARITY (SORT NAME)* RESULT_SORT RESULT_NAME`, arguments
first and result last. Sorts are Int/Bool; table-only variables are registered
and exported without artificial clauses. Even N=0 requires the explicit tail.
Without this flag the original v1 policy remains in use and rejects UF tails.

All observed modes require native CNF plus UF congruence for SAT. None disables
feedback/candidates and conservatively returns unknown at a pure UF conflict.
Soft adds exact incident-bucket deltas and singleton repairs. Joint also proposes
whole result groups and argument separation, with independent pool quotas and
minority-result coverage. Materialized integer observations can use bounded
unit-linear compensation (12 touched rows / 16 variables), while whole result
groups have a separate 64-variable cap. Count uses unit weights; paws enables
the mature clause-weight mechanism and global UF pressure. Final Python source
model validation remains mandatory in the driver.

The v12 observed control policy is reported as `uf_search_policy=adaptive_tabu_v12`
while retaining the v2 wire/version identity. UF-only iterations obey deadline
and restart checks, PAWS UF pressure changes trigger a fresh candidate comparison,
and count keeps unit weights. UF patches respect typed-clock tabu/immediate
reversal, with same-trial SAT/new-raw-best aspiration. All-tabu waiting advances
the Boolean clock and permits one explicit override after eight waits.
`current_arithmetic_unsat` and `current_uf_conflicts` describe the same final
state; `best_total_arithmetic_unsat` and `best_total_uf_conflicts` describe the
same historical minimum-total state. These fields are null before initialization.
The old `best_arithmetic_unsat` remains a separate historical CNF minimum.

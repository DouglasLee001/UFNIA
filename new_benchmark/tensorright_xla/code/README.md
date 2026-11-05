# TensorRight integer rewrite models

This directory is self-contained with Python 3.10+ and the pinned dependency in requirements.txt (z3-solver 4.16.0). It contains the model generator, independent concrete integer replay, and common/smt_tools.py for complete-query extraction and strict typed-AST auditing.

From the new_benchmark directory, reproduce the original retained eight-instance campaign with:

~~~bash
python3 tensorright_xla/code/generate.py --mode campaign
~~~

Install the dependency in your chosen Python environment with "python3 -m pip install -r tensorright_xla/code/requirements.txt" if needed. The default mode is also campaign. "--case NAME" selects one listed configuration; "--output-root DIR" generates a separate output tree.

The 28 model configurations retain their original equations, preconditions and full negated rewrite obligations. Publication applies the requested experiment guard in known_unsat.json: the 18 IDs previously recorded as UNSAT are skipped before staging. This exact ID list also applies to explicit --case requests and generate_direct; it does not infer a result for unlisted or unknown inputs. The original five-case --mode pilot is entirely known UNSAT and now reports five skipped cases without writing instances.

Among the ten remaining campaign configurations, slice_broadcast_rank2 and slice_broadcast_rank3 are excluded because positive-arity UF applications disappear after lightweight simplification. The default/campaign entry point therefore writes eight eligible formulas from an empty output directory. It stages legacy raw/task/input files only in a TemporaryDirectory under /tmp and removes that staging tree on exit. It does not solve the generated queries or relabel them SAT; newly generated files have status unknown. Existing files with the same parsed assertions retain their bytes and recorded headers; a different existing formula is rejected.

Each formula asks for a counterexample to validity, shape, legal output access or pointwise integer-element equality under the recorded preconditions. A SAT result would identify a violation within this fixed-rank application-derived model. All input extents are positive, slices are nonempty, padding is nonnegative, and integer elements are unbounded. This does not establish IEEE floating-point or machine-integer equivalence. The symbolic-stride nested_slice model is an explicit generalization of upstream rule07.

Source provenance is retained in source-pin.json: TensorRight commit b3e3a533ff67568db127783cc353ebe04142929f, fixed upstream paths and SHA256 values. The upstream Apache-2.0 notice is retained in LICENSE.TensorRight. Source links remain usable without the removed top-level upstream, locks, metadata or finalizer directories. Existing shipped instance headers retain their original historical metadata/license paths; use source-pin.json and LICENSE.TensorRight here for the retained provenance and notice. The Haskell verifier was not built or run.

The low-level generate(root, selected, argv) API retains all original configurations for temporary staging and semantic tests; the known-UNSAT guard applies to publishing through generate_direct and the CLI. The command-line entry point uses generate_direct. Its regression checks can be run without satisfiability calls:

~~~bash
python3 -m unittest discover -s tensorright_xla/code -p test_compact_generation.py -v
~~~

The older test_semantics.py also contains bounded solver-based witness tests; those are separate from generation and the compact-layout regression.


## Larger composed SAT candidates

generate_harder.py is a separate entry point; it does not change generate.py, the original eight SAT cases, or known_unsat.json. Its default two profiles are rank2/depth2/two observations and rank3/depth3/two observations. Each stage composes the fixed TensorRight-derived Concat, Reverse, Pad and Slice operations. Only the final stage uses the existing deliberately incorrect naive_mirror transformation.

All stages share the same symbolic input UF and stride vector; observation coordinates share a tile base and displacement. The profile requires both outputs to be valid and equal-shaped and all observations to come from actual input elements, rather than padding. The entire selected observation property is negated, including validity, shape, access and value equality. These explicit preconditions restrict the search to source-backed strided tiles; a mismatch is a real counterexample within this model, while an UNSAT result would not establish unrestricted tensor-rewrite correctness.

From the new_benchmark directory, generate the default two candidates and retain their validation certificates outside the dataset:

~~~bash
python3 tensorright_xla/code/generate_harder.py --proof-dir /tmp/tensorright-harder-proofs
~~~

Use "--output-root /tmp/tensorright-harder-candidates" to inspect an isolated output tree before publishing. "--list" also lists the optional rank2/depth3/three-observation and rank4/depth4/three-observation profiles; "--case NAME" selects one.

A file receives status sat only after the local strict AST/research audit, an explicit integer and total-UF valuation satisfying every original assertion, and independent Python-only operator/coordinate replay all pass. No solver.check is called. The deterministic certificate chooses a concrete row-major interpretation of A only for validation; its extent, stride and parameter assignments are never added to the candidate formula. Failed certificates produce no candidate for that case. Default proofs are temporary files cleaned from /tmp; an explicit --proof-dir must also lie under /tmp. Existing identical formulas preserve their bytes, and differing formulas are not overwritten.

These are application-derived compositions and deliberate model mutations. The exact chain is not claimed to be an existing upstream rewrite, a faithful native Haskell export, or a production compiler bug. Larger source extents in a concrete certificate are witness values rather than constants used to inflate the symbolic benchmark.

Public functions for review are build_case(rank, depth, lanes), snapshot_text(case), certificate_parameters(case), verify_certificate(case, text=None, parameters=None), replay_certificate(case, parameters), and generate_harder(root=None, selected=None, proof_dir=None). Regression tests freeze the original two assertion fingerprints and prohibit solver.check:

~~~bash
python3 -m unittest discover -s tensorright_xla/code -p test_harder_generation.py -v
~~~


## Published larger SAT set and bounded measurements

The instance directory now contains the original eight SAT files and four additional certified SAT files (12 total). To reproduce all four additions from the repository root:

~~~bash
for task_case in \
  coupled_mirror_rank2_depth2_lanes2 \
  coupled_mirror_rank2_depth3_lanes3 \
  coupled_mirror_rank3_depth3_lanes2 \
  coupled_mirror_rank4_depth4_lanes3
do
  python3.12 -B new_benchmark/tensorright_xla/code/generate_harder.py --case "$task_case"
done
~~~

Measurements on node2 used Z3 5.1.0, cvc5 1.4.2 and Yices 2.7.0 with one solver thread, a 4 GiB address-space cap and a 30-second wall deadline. At most three external solver processes ran concurrently. Yices used --mcsat --nthreads=0; Z3 used -smt2 parallel.enable=false smt.threads=1; cvc5 used --lang=smt2. Times below include process startup; >30 means timeout, not an UNSAT or unknown classification.

| Profile | SMT2 bytes | Z3 seconds | cvc5 seconds | Yices seconds |
| --- | ---: | ---: | ---: | ---: |
| rank2/depth2/lanes2 | 37,201 | >30 | >30 | 0.162 |
| rank2/depth3/lanes3 | 86,823 | >30 | >30 | >30 |
| rank3/depth3/lanes2 | 88,501 | >30 | >30 | >30 |
| rank4/depth4/lanes3 | 230,088 | >30 | >30 | >30 |

All four remain certified SAT even when the external solvers time out. Independently replayed total-UF valuations assigning only 0 or 1 to input data also satisfy all assertions; the row-major certificate does not impose large element values on the symbolic formula. These measurements establish a 30-second lower bound for the timed-out runs only. They do not establish difficulty at a 600-second budget or for UFNIA_LS.

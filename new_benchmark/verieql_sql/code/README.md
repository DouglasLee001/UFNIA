# VeriEQL SQL generator

此家族只保留 `code/` 和 `instance/`。原有 3 个 SAT 实例：Orders 每件加收一分钱的派生查询，分别为2、3、4行；保留文件字节不变，行数变体保持同一查询任务身份。

生成入口直接把通过严格 AST 检查的完整 QF_UFNIA 单查询写入 `instance/`。原始 String 公式、标签域转换版本、模型、逐任务记录及失败资料仅在 `/tmp/ufnia-verieql-*` 的 TemporaryDirectory 内使用，运行结束或异常退出时清理。正常输出目录只包含最终 `.smt2`；简要运行结果输出到 stdout，没有外置 metadata、raw、validation 或 summary 文件。

从仓库根目录运行：

```bash
# 默认重现 Orders SAT pilot；已知UNSAT的交换律配置自动跳过
python3.12 -B new_benchmark/verieql_sql/code/generate.py

# 已知UNSAT的公开 Calcite 配置只报告跳过，不恢复实例
python3.12 -B new_benchmark/verieql_sql/code/generate.py   --tasks new_benchmark/verieql_sql/code/campaign-inputs.json   --task-id calcite_149 --bounds 2 --output /tmp/ufnia-sql-final

# 固定campaign跳过17个已知UNSAT配置，仅重现3个保留的SAT配置
python3.12 -B new_benchmark/verieql_sql/code/run_campaign.py

# 测试的输入和输出均在 /tmp，测试结束自动清理
PYTHONDONTWRITEBYTECODE=1 python3.12 -B -m unittest discover   -s new_benchmark/verieql_sql/code/tests -v
```

同名实例存在时，生成器检查解析展开后的公式指纹；一致则保留既有文件字节，不重写头部，公式不同则报错拒绝覆盖。原有3份保留文件的历史头部可能引用旧交付布局中的 metadata 路径；那些外置资料已按精简要求移除，文件本身不改。

`generate.py` 默认对每个实际 Z3 检查使用60秒限时和单线程，进程地址空间上限4GiB。`--timeout-ms`、`--bounds`、`--task-id` 和 `--output` 可显式指定。Python hash seed自动固定为0，避免原工具的字符串常量哈希随进程变化。空前提、未知/不一致的求解结果、SAT SQL回放失败或理论审计失败均不发布实例。unknown和超时保持真实状态，不称作UNSAT，也不会因此删除既有unknown文件。

`known_unsat.json` 记录此前实际验证为UNSAT、已按用户要求移除的17个配置ID。正常的 `generate_selected` 和 `run_campaign.py` 在调用原生编码器前跳过这些ID；对其他配置，如果本次原始和变换查询都实际返回UNSAT，发布器也不写实例。原应用编码和语义变换没有改变；低层 `_generate_snapshot` 仍供 `/tmp` 中的模型语义测试使用，不属于正常发布入口。

`common/smt_tools.py` 是此家族自带的完整命令作用域提取与严格理论检查器，支持原有必要的等价移植正规化，包括单元 `(+ x)`、`(* x)` 消去以及正模数 divisible 降低；不依赖已删除的根目录 tools、finalizer 或 schema。最终查询包含全部活跃断言、当次 assumptions 和反例终式，并只保留一个 check-sat。

官方 VeriEQL 固定为 v1.0、commit `3b99928bf976c11cb59c9db6a177adba21f75798`。`upstream/src/` 保存官方源文件，`upstream-lock.json` 记录归档和许可证身份。恢复依赖：

```bash
bash new_benchmark/verieql_sql/code/setup.sh
# 仅恢复/核验官方源码：
python3.12 new_benchmark/verieql_sql/code/fetch_upstream.py
```

源码与依赖均保存在本 `code/` 下：`fetch_upstream.py` 核验固定归档 SHA256及每个官方 Git blob；`requirements-runtime.lock` 固定 Python依赖，安装目标为 `vendor/`，不改全局环境。`setup.sh` 不保留 pip下载缓存。`compat.py` 只在进程内适配原工具的 context 参数调用；官方源码和已安装Z3文件不修改。

原SQL模式、完整性约束、NULL及bag重复计数都由固定官方编码器保留。行数参数表示每张基表的**确切行数**，不表示任意规模等价。Orders金额明确使用整数分；公开模式中的VARCHAR/DATE/BOOL依旧交给原工具的原生表示，不伪称全部字段原本为INT。

String标签是符号常量，允许别名；变换将整个只用于相等和UF参数的标签域替换为一个自由sort，保留所有别名关系。该步骤的关系是等可满足，随后单元算术正规化是等价，见 [TRANSFORMATION.md](TRANSFORMATION.md)。量词、字符串字面量/字符串运算以及目标sort冲突都拒绝转换。

保留的SAT Orders任务实际在SQLite执行两条SQL并比较含NULL的bag结果；没有MySQL server回放声明。新增任务若超出共享整数SQL子集，需要独立检查方言、范围和溢出条件。源许可证为CC BY-NC-SA 4.0；公式再分发权仍需审核，不自动改成CC BY。

## Larger selected SQL SAT configurations

The independent harder entry point reproduces four reviewed configurations by default: overcharge at 12 and 16 rows, a three-table loyalty join-key error at 3 rows, and an incorrect line-discount aggregation pushdown at 3 rows.

```bash
# Final selected four cases; writes final SMT2 to this family's instance/
python3.12 -B new_benchmark/verieql_sql/code/generate_harder.py

# Reproduce the selected four into /tmp for review
python3.12 -B new_benchmark/verieql_sql/code/generate_harder.py --output /tmp/ufnia-sql-hard-review

# Explicit exploration is restricted to /tmp and keeps the shorter solver budget
python3.12 -B new_benchmark/verieql_sql/code/generate_harder.py --probe --case overcharge --bound 24 --output /tmp/ufnia-sql-hard-probe
```

The selected campaign runs one worker at a time, with a 4GiB address-space limit, 30-second original/transformed solver budgets, and a hard 120-second deadline per worker. Before final publication, the real original and transformed checks must both return SAT; every active assertion and assumption is evaluated with the actual returned model, both concrete SQLite replays must pass, and the strict QF_UFNIA audit must pass. Existing identical formulas retain their exact bytes; different existing formulas are never overwritten.

All proof models, complete private query snapshots, JSON evidence and logs stay under `/tmp`; none of the witness values or proof material is added as a candidate constraint. The default generator and known-UNSAT policy remain unchanged. The original baseline SAT files retain their bytes.

`harder-inputs.json` keeps the source inputs and the four default case/bound selections. Exploration of other listed cases requires `--probe`, whose output must be under `/tmp` and whose default solver budget is 10 seconds. Original/converted unknown outcomes remain unknown and cannot be recorded as verified SAT.

The loyalty query returns an exact discounted-charge numerator in hundredths of a cent, with no division or rounding. Complete product/customer key domains ensure that the wrong loyalty key still joins exactly one row, so the error is in rate-dependent arithmetic rather than missing result rows. Formal quantity, price, rate and credit bounds keep every intermediate and aggregate value below signed 64-bit limits. NULL/bag/native-type encodings remain in place; floating SQLite results are rejected.

Larger byte or UF counts alone do not establish difficulty. The evidence records actual timings, strict AST features, resource use, full-assertion model checks and SQL replay results; external solver profiles determine which configurations are difficult.


## Published larger SAT set and bounded measurements

The instance directory now contains the original three SAT files plus the four selected larger SAT files (seven total). The generate_harder.py command above reproduces all four additions.

Measurements on node2 used Z3 5.1.0, cvc5 1.4.2 and Yices 2.7.0 with one solver thread, a 4 GiB address-space cap and a 30-second wall deadline. At most three external solver processes ran concurrently. Yices used --mcsat --nthreads=0; Z3 used -smt2 parallel.enable=false smt.threads=1; cvc5 used --lang=smt2. Times below include process startup; >30 means timeout.

| Profile | SMT2 bytes | Z3 seconds | cvc5 seconds | Yices seconds |
| --- | ---: | ---: | ---: | ---: |
| Orders overcharge, 12 rows | 178,755 | 6.769 | 0.815 | 0.950 |
| Orders overcharge, 16 rows | 314,353 | 1.511 | >30 | 6.587 |
| Loyalty wrong join, 3 rows | 2,449,606 | 8.653 | >30 | >30 |
| Line discount pushdown, 3 rows | 2,540,665 | 4.056 | 16.210 | >30 |

Every published formula passed both original/transformed full-assertion model validation and concrete SQL replay before these external measurements. Increasing the row bound does not guarantee a monotonic time increase, as the two overcharge profiles demonstrate. These are application-derived SQL counterexamples, and the timing results do not establish difficulty at a 600-second budget or for UFNIA_LS.

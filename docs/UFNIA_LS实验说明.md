# 最新全量正式结果

2026-11-03，当前清理版a6在node3全572／每例端到端600／实际峰180：419有效SAT、153timeout，独立审计通过。原v12为413，本轮新增9、未复现3、净增6；20240410为68／191，历史该子集最好70仍保留。[本轮报告](../result/best_node3_all_600_20261103_161710_HtUKw3/REPORT.md)。旧reserve48GiB、本轮24GiB，不把总耗时差异单归算法。下文较早“尚未重跑全量”属于此前状态，当前编译和运行命令继续可用。

# 当前最佳实现与运行

2026-11-03：当前工作区已采用20240410-certora最好70／191对应的affine＋精确缓存策略，POINT分支已移除。原最佳冻结版完整保留在result/certora2024/snapshots/affine_point_units_v1；后续较差实验只保留结果、报告及历史manifest，其实现/编译副本已清理。

~~~bash
cd /home/lbh/smt/ufnia
make -C UFNIA_LS mature
UFNIA_AFFINE_VIEWS=1 UFNIA_AFFINE_FOOTPRINT_CACHE=1 UFNIA_AFFINE_PROGRESS=1 \
timeout --signal=KILL 600s ./run.sh INPUT.smt2 \
  --backend mature --uf-observers --interaction joint --scoring paws \
  --seed 0 --seconds 600 --preprocess-timeout 600
~~~

此命令显式选mature、UF observers、joint、PAWS、seed0，外预算包含预处理、搜索和验证。compile及验证只在node2/node3执行；本次清理没有启动新的真实benchmark轮。主目录新binary a6e8de9a…的验证为70项回归全过，70／191历史成绩仍绑定原28cf0f1f…，不混为新的完整性能成绩。

[清理记录](../result/cleanup_best_20261103/REPORT.md)、[当前结构](../运行文档/当前结构设计.md)。以下保留历史v12的572例验收及复现记录。

# UFNIA_LS：mature＋UF 观察模式实验说明

状态（2026-10-30）：**正式验收通过，572例中413个有效SAT、159个超时**。仅在node3运行，实际并发峰值180，每例端到端600秒；没有新增OOM。以下结果来自单一冻结配置，不合并不同版本或配置的SAT。

已实现结构和历史尝试见[当前结构设计](../运行文档/当前结构设计.md)，剩余工作见[待尝试方向](../运行文档/待尝试方向.md)。本文保留历史正式轮复现。2026-10-31最新授权为node4、node5每台各180并行，500 SAT目标仍未达到；下文node3命令仅用于复现旧v12正式轮，不能替代新目标验收。

## 架构与参数

`--backend mature` 参考成熟 NIA_LS 局部搜索内核，将整数、系数、边界及精确求值迁移到 GMP，保留 Bool/Int 阶段、增量子句评分、PAWS 和 tabu。默认后端仍为 `core`，mature 需要显式选择。

Z3 负责解析、UF 抽象、算术 lowering、CNF 转换和 Goal 模型恢复，不调用 `Solver.check()` 或完整 SMT 搜索。观察 marker 与公式经过同一 tactic 链，因此保留 solve-eqs 等代换。随后将观察表达式物化为 typed Int/Bool 变量及精确定义，把 UF 表附在同一个 native 文件中。

搜索按函数及当前实参建立桶，增量更新 UF 冲突。候选包括单变量移动、结果组联合移动、实参分离，以及保持部分线性等式的有界补偿。UF-only 平台也执行 PAWS、tabu 与停滞重启；aspiration 使用同一候选状态的 CNF/UF 违例数。

| 选项 | 行为 |
|---|---|
| `--interaction none` | 关闭 UF 反馈与 UF 候选；最终仍要求同余成立，否则返回 unknown |
| `--interaction soft` | 将 UF 冲突纳入评分，并生成单变量 UF 候选 |
| `--interaction joint` | 增加结果组联合移动与有界线性补偿；正式配置使用此项 |
| `--scoring count` | CNF 与 UF 使用单位权重，不执行 PAWS 增权 |
| `--scoring paws` | 使用成熟子句增权和 UF 压力增权；正式配置使用此项 |

上述选项在 mature 下要求 `--uf-observers`。不启用观察时，mature 保留内建策略与 eager/lazy 外部 UF 精化。`legacy/mixed` 等 core 后端策略并不控制 mature；以报告的 `effective_options` 为准。

只有 native CNF 与 UF 同余精确验证通过，且恢复模型满足原始断言，才发布 SAT。unknown、超时和未验证候选不计 SAT，也不宣告 UNSAT。原始语料只读，结果放在本地 `result/`，不纳入 Git。

原式验证使用恢复后的 Z3 模型求值（`model_completion=True`）。另有只读的显式模型宏展开检查：对先完成的413例，322例可直接把全部原断言化简为true，91例仅留下`div/mod`零除残余、没有false或缺失UF解释；该严格检查器不补值，故这91例记为unverified，不宣称全部模型都通过了这种额外重放。逐例原式验证与最终结果审计是正式SAT计数口径，补充报告见`result/ufnia_search/independent_v12_models/20261030T181632Z-791d1809/summary.json`。

## 冻结版本与编译

冻结目录：`result/ufnia_search/uf_mature_uf_v12_source`；协议 `mature-mpz-v2`，搜索标识 `adaptive_tabu_v12`。

冻结 binary SHA256：`641a0c41ffa8d1a8507d69815743e0497b662704d1e112adad7a61eb596e4667`。

本地编译需要 Python 3、z3-solver（本轮 Z3 4.16.0）、C++17 编译器和 GMP 开发库。编译工作区源码，不改写冻结目录：

```sh
cd /home/lbh/smt/ufnia
make -C UFNIA_LS native-frontend mature
```

## 单文件运行

以下使用现有冻结版本。内部 `--seconds` 是每 query 搜索/精化预算；外层硬超时将解析、预处理、搜索和验证一起限制为 600 秒。

```sh
cd /home/lbh/smt/ufnia
ufnia_frozen="$PWD/result/ufnia_search/uf_mature_uf_v12_source"
ufnia_case='/data/shared/lbh/QF_UFNIA/sat/QF_UFNIA/20230314-Jaroslav-Bendik-Certora/11775_ad46e5b8db4748c51973_42_QF_UFNIA.smt2'
mkdir -p result/ufnia_search/manual_v12
timeout --signal=KILL 600s python3 "$ufnia_frozen/UFNIA_LS/run.py" "$ufnia_case" \
  --backend mature --uf-observers --interaction joint --scoring paws \
  --seconds 600 --seed 0 --preprocess-timeout 600 \
  --model-output result/ufnia_search/manual_v12/model.smt2 \
  --json-report result/ufnia_search/manual_v12/report.json
```

## node3 全量复现

正式轮已结束。需复现时登录 node3，使用新的输出目录，并确保没有另一轮任务占用实验资源。以下与实际 launcher 参数一致；内存 profile 只控制准入与顺序，不筛选或合并求解结果。未指定 manifest/limit，遍历全 572 例。

```sh
cd /home/lbh/smt/ufnia
ufnia_frozen="$PWD/result/ufnia_search/uf_mature_uf_v12_source"
python3 "$ufnia_frozen/scripts/run_ufnia_experiments.py" \
  --solver-root "$ufnia_frozen" \
  --benchmarks /data/shared/lbh/QF_UFNIA/sat/QF_UFNIA \
  --output "$PWD/result/ufnia_search/reproduce_mature_uf_v12_node3" \
  --require-host node3 --jobs 180 --timeout 600 \
  --memory-profile "$PWD/result/ufnia_search/mature_v12_memory_profile.csv" \
  --memory-reserve-gb 48 --order memory \
  --solver-args '["--backend", "mature", "--seconds", "600", "--seed", "0", "--preprocess-timeout", "600", "--uf-observers", "--interaction", "joint", "--scoring", "paws"]'
```

runner 对每个完整进程树执行 600 秒外预算，覆盖解析、预处理、搜索、模型恢复与结果发布；达到预算的结果不能计 SAT。180 是任务并行上限，内存准入可能使部分任务等待。

## 正式结果与审计

正式 run：`result/ufnia_search/formal600_mature_uf_v12_node3/run-20261030T175622Z-yenkmuh9`。

| 配置 | 完成/总数 | 独立验证 SAT | unknown/timeout/其他 | 最终验收 |
|---|---|---|---|---|
| v12 mature＋joint＋paws，seed 0 | 572/572 | 413 | 159 timeout，其余0 | 通过 |

| family | 基准数 | 有效SAT | timeout |
|---|---:|---:|---:|
| 201903-Zohar-alive | 107 | 103 | 4 |
| 201903-Zohar-ic | 181 | 180 | 1 |
| 20190906-CLEARSY | 8 | 8 | 0 |
| 20190909-CLEARSY | 23 | 23 | 0 |
| 20230314-Jaroslav-Bendik-Certora | 62 | 38 | 24 |
| 20240410-certora | 191 | 61 | 130 |
| total | 572 | 413 | 159 |

本地结果：[summary.csv](../result/ufnia_search/final_v12/summary.csv)、[detailed.csv](../result/ufnia_search/final_v12/detailed.csv)、[完整目标验收](../result/ufnia_search/final_v12/acceptance.json)、[来源与SHA](../result/ufnia_search/final_v12/provenance.json)。独立审计为上述正式run下`audit-3b3ei85q/audit.json`，issues为空；其`family_summary.csv`与`detailed.csv`保留完整审计字段。

最终验收明确核对572个唯一输入、实际180并发、工具与输入SHA稳定、每query原式验证和发布模型SHA、当前CNF/UF均0以及同一配置413个SAT均小于600秒；最大SAT墙钟559.272594秒。总耗时2547.870秒。`check_final_v12.py`的13项完整目标检查全部通过，输出已存`final_v12/acceptance.json`。266项回归及36项核心差分验证通过。

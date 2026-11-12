# QF_UFNIA 全集消融报告


原始结果与完整性证据：[实验原报告](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/REPORT.md)、[分族汇总](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/summary.csv)、[逐例结果和时间](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/detailed.csv)、[配对统计](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/comparisons.csv)、[新增/丢失清单](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/sat_changes.csv)、[最终接受记录](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/ACCEPTANCE.json)。本文件保留报告正文，原始实验工件仍在 Git 忽略的 result 目录。

无缓存条件下启用仿射后SAT净增6例；共同SAT耗时比几何均值0.970917，须结合其中位数与逐例得失理解，不能称为全面加速。

仿射开启时缓存SAT净增0例；共同SAT耗时比几何均值1.030459，约3.05%的单次配对比值改善，未作显著性推断。

完整配置相对关闭仿射/缓存基线的全集PAR2均值降低2.77%；这是组合效果。

生成时间（北京时间）：2026-11-12 10:20:22 CST。共572例，三组均finished，runner与独立audit退出码均为0。

独立审计接受SAT：full=419，no_cache=419，no_affine=413。
缓存、无缓存条件下的仿射、组合增量分别使用full−no_cache、no_cache−no_affine、full−no_affine；组合差异不能解释为单独仿射效果。

## 配置与完整性

运行主机node3；输入根目录`/data/shared/lbh/QF_UFNIA/sat/QF_UFNIA`；seed=0；每例600秒完整进程树墙钟预算，覆盖解析、预处理、搜索和原模型验证。预处理上限600秒被包含在同一600秒总预算内。
并发上限180；三个配置按full、no_cache、no_affine顺序运行；内存profile排序，预留24 GiB；估计为profile峰值×1.5且至少256 MiB。OMP/OpenBLAS/MKL/NumExpr线程数均为1；PYTHONHASHSEED=0。

| 配置 | 仿射视图 | footprint缓存 | 开始（北京时间） | 结束（北京时间） |
| --- | --- | --- | --- | --- |
| full | 1 | 1 | 2026-11-11 20:51:52 CST | 2026-11-11 21:24:41 CST |
| no_cache | 1 | 0 | 2026-11-11 21:25:27 CST | 2026-11-11 21:58:25 CST |
| no_affine | 0 | 0 | 2026-11-11 21:58:31 CST | 2026-11-11 22:30:57 CST |

统一solver参数：`--backend mature --uf-observers --interaction joint --scoring paws --seed 0 --preprocess-timeout 600.0`。backend=mature / mature_uf_observed；interaction=joint；scoring=paws。

本脚本核对源输入全集及572个SHA、57个snapshot资产、外部依赖、profile、runner、源码/二进制SHA、solver参数、seed和内存策略。三组运行manifest顺序完全一致；运行manifest与input_manifest比较全集，后者不要求同序，因为运行器按内存profile重排。
独立audit evidence_sha256锁定run.json、summary.json、manifest.json与results.csv；报告逐行核对audit detailed与结果、输入SHA、耗时、family和严格布尔verified_sat，仅verified_sat=true计SAT。

## 分family的SAT与差值

| family | 例数 | full | no_cache | no_affine | 缓存差 | 无缓存仿射差 | 组合差 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 201903-Zohar-alive | 107 | 103 | 103 | 103 | 0 | 0 | 0 |
| 201903-Zohar-ic | 181 | 179 | 179 | 180 | 0 | -1 | -1 |
| 20190906-CLEARSY | 8 | 8 | 8 | 8 | 0 | 0 | 0 |
| 20190909-CLEARSY | 23 | 23 | 23 | 23 | 0 | 0 | 0 |
| 20230314-Jaroslav-Bendik-Certora | 62 | 38 | 38 | 38 | 0 | 0 | 0 |
| 20240410-certora | 191 | 68 | 68 | 61 | 0 | 7 | 7 |
| TOTAL | 572 | 419 | 419 | 413 | 0 | 6 | 6 |

## 配对比较与逐例得失

新增/丢失均相对with配置：新增=with独立验证SAT且without未验证SAT；丢失反之；retained为两者共同验证SAT。净增=新增−丢失。
共同SAT配对耗时比r=t_without/t_with；r>1表示with更快。几何平均为exp(mean(log(r)))，中位数直接在逐例比值上计算；仅在共同SAT集合比较，存在幸存者选择偏差。逐例耗时与比值见detailed.csv，新增/丢失逐例见sat_changes.csv。

| 比较 | 新增 | 丢失 | 共同SAT | 净增 | 耗时比几何均值 | 耗时比中位数 | with更快 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full_vs_no_cache | 1 | 1 | 418 | 0 | 1.030459 | 1.007990 | 217 |
| no_cache_vs_no_affine | 9 | 3 | 410 | 6 | 0.970917 | 1.031213 | 240 |
| full_vs_no_affine | 9 | 3 | 410 | 6 | 1.001043 | 1.056722 | 254 |

缓存增量（仿射开启）：净增0例；共同SAT耗时比几何均值1.030459、中位数1.007990；with更快217例、without更快201例。

### full_vs_no_cache：缓存增量（仿射开启）

新增：

- `20240410-certora/lia/0016.smt2`

丢失：

- `20240410-certora/lia-splits/0052.smt2`

无缓存条件下的仿射增量：净增6例；共同SAT耗时比几何均值0.970917、中位数1.031213；with更快240例、without更快170例。
几何平均与中位数方向相反，说明耗时变化分布不均；该单次配对结果不支持笼统称为整体加速。

### no_cache_vs_no_affine：无缓存条件下的仿射增量

新增：

- `20240410-certora/lia-splits/0044.smt2`
- `20240410-certora/lia-splits/0052.smt2`
- `20240410-certora/lia/0007.smt2`
- `20240410-certora/lia/0042.smt2`
- `20240410-certora/lia/0056.smt2`
- `20240410-certora/lia/0057.smt2`
- `20240410-certora/nia-splits/0026.smt2`
- `20240410-certora/nia-splits/0033.smt2`
- `20240410-certora/nia/0046.smt2`

丢失：

- `201903-Zohar-ic/int_check_bvsge_bvurem1_rtl.smt2`
- `20240410-certora/lia/0009.smt2`
- `20240410-certora/nia/0036.smt2`

仿射与缓存的组合增量：净增6例；共同SAT耗时比几何均值1.001043、中位数1.056722；with更快254例、without更快156例。
几何平均接近1，单次共同SAT配对耗时整体接近持平。

### full_vs_no_affine：仿射与缓存的组合增量

新增：

- `20240410-certora/lia-splits/0044.smt2`
- `20240410-certora/lia/0007.smt2`
- `20240410-certora/lia/0016.smt2`
- `20240410-certora/lia/0042.smt2`
- `20240410-certora/lia/0056.smt2`
- `20240410-certora/lia/0057.smt2`
- `20240410-certora/nia-splits/0026.smt2`
- `20240410-certora/nia-splits/0033.smt2`
- `20240410-certora/nia/0046.smt2`

丢失：

- `201903-Zohar-ic/int_check_bvsge_bvurem1_rtl.smt2`
- `20240410-certora/lia/0009.smt2`
- `20240410-certora/nia/0036.smt2`

## 全集PAR2与资源

PAR2覆盖全部572例：独立验证SAT使用其完整进程树墙钟耗时，所有非SAT（含timeout/unknown/error等）统一记1200秒。PAR2越低越好；不把600秒之后的timeout清理开销当作SAT耗时。

| 配置 | SAT | 非SAT | PAR2总秒 | PAR2均秒 | 轮墙钟秒 | 峰值并发 |
| --- | --- | --- | --- | --- | --- | --- |
| full | 419 | 153 | 196044.299919 | 342.734790 | 1968.927230 | 180 |
| no_cache | 419 | 153 | 197052.651821 | 344.497643 | 1977.781083 | 180 |
| no_affine | 413 | 159 | 201629.222238 | 352.498640 | 1946.095195 | 180 |

| 配置 | 最大单例RSS GiB | 单例RSS中位MiB | 采样CPU总秒 | 内存预留峰GiB | 预算GiB | 主机OOM增量 | 逐行OOM增量和 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full | 15.271782 | 87.587891 | 100023.050000 | 101.364887 | 101.374092 | 0 | 0 |
| no_cache | 15.245796 | 91.712891 | 101073.130000 | 101.342857 | 101.374092 | 0 | 0 |
| no_affine | 15.227322 | 107.035156 | 101811.480000 | 101.363176 | 101.374092 | 0 | 0 |

| 配置 | sat | timeout | unknown | error | killed | resource_error | interrupted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full | 419 | 153 | 0 | 0 | 0 | 0 | 0 |
| no_cache | 419 | 153 | 0 | 0 | 0 | 0 | 0 |
| no_affine | 413 | 159 | 0 | 0 | 0 | 0 | 0 |

Campaign北京时间：2026-11-11 20:51:51 CST 至 2026-11-11 22:31:01 CST。
三轮runner墙钟合计5892.803508秒；campaign起止（含控制、审计及恢复间隔）5949.887094秒。总轮耗时不是单例600秒预算；180路调度与内存准入等待决定轮耗时。
RSS/CPU每0.1秒采样进程树；RSS为最大单例采样峰值，不代表全机同时驻留内存；内存预留峰是准入估算，不能当成实测RSS。采样CPU可能漏掉短命进程。主机OOM计数是全机指标，逐行增量也可能重叠，不能据此归因到单例。

| 配置 | CPU/亲和核数 | 起始load(1/5/15) | 结束load(1/5/15) | 主机OOM前→后 | 准入等待总秒 |
| --- | --- | --- | --- | --- | --- |
| full | 192/192 | [0.10986328125, 0.08154296875, 0.02880859375] | [1.5712890625, 6.29296875, 22.8701171875] | 64→64 | 16105.097246 |
| no_cache | 192/192 | [0.91064453125, 5.3818359375, 21.68994140625] | [1.47705078125, 6.1591796875, 25.3759765625] | 64→64 | 15971.186044 |
| no_affine | 192/192 | [1.4384765625, 6.0732421875, 25.24462890625] | [1.8046875, 6.79541015625, 27.0498046875] | 64→64 | 16625.528532 |

## 验证范围与限制

运行时原模型验证针对每个SMT-LIB查询的原始断言（original_assertions），要求完整查询覆盖、每query original_model_validated=true；mature原生验证另覆盖CNF与UF同余且无UF冲突。只有所有查询均成功才计整文件SAT。模型工件包含每查询的模型，原始断言验证用于排除仅满足转换后公式的伪解。
独立audit检查保留工件、校验和、后端配置、查询覆盖、原模型验证记录及模型结构；它不调用求解器，也不重新求值模型。本次报告只消费公开结果与metadata，未打开native/model/assignment/private progress，未重新运行求解。
flag_checks.json记录所有SAT均有原生开关证据且不匹配数为0。真实三组各563例有可用记录，其余9例均timeout；未产生可用原生开关记录不代表已定位具体耗时阶段。报告使用既有开关审查结果，不重新读取原生工件。
本次仅单种子（seed=0）和固定执行顺序，不能估计随机种子方差、消除顺序/负载偏差或宣称统计显著性。SAT数量相同不意味着解出的集合相同；缓存收益也可能因预算边界产生得失。full−no_cache测仿射开启时的缓存增量；no_cache−no_affine测无缓存时的仿射增量；full−no_affine仅为组合增量，缓存依赖仿射，因此affine=0/cache=1不能构成独立有效的缓存对照。三组属于有意义的嵌套消融，仅支持上述两个条件增量和组合比较，不能从中估计一般二因素交互效应。

## 科学图

仅计独立审计验证的SAT；散点仅包含每对共同SAT。横轴without、纵轴with，落在对角线下方表示with更快。

![Cactus：验证SAT与完整墙钟耗时](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/cactus.png)

[导出cactus PDF](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/cactus.pdf)

![三对共同SAT耗时散点](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/scatter.png)

[导出scatter PDF](../result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/scatter.pdf)

## 机制触发观测

补充的公开标量mechanism_observations.json采用每例最终core记录，否则取最后保留progress；不含timeout尾段，不能视为完整真实运行计数。这里只引用已提取标量，不打开原始工件。

| 配置 | 观测例数 | 最终core | 最后progress | affine提交>0例数 | cache命中>0例数 | cache条目最大 | cache charged bytes最大 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full | 563 | 419 | 144 | 385 | 431 | 64 | 4193840 |
| no_cache | 563 | 419 | 144 | 385 | 0 | 0 | 0 |
| no_affine | 563 | 413 | 150 | 0 | 0 | 0 | 0 |

这些观测证明机制曾触发；cache charged bytes是内部记账且不能当作RSS，跨例相加也不是并发峰值。观测缺失仅9个timeout，未凭缺失推断具体耗时阶段；不从机制计数推断因果收益。

## harness恢复说明

首版控制器在full完成后读取旧字段peak_concurrent_cases，触发KeyError；恢复控制器改读actual_peak_concurrent_cases。已完成full runner保留且退出码0，未重启实验，未修改solver源码或二进制。原记录保留于harness_recovery.json与controller_attempt1_status.json。

```json
{
  "at": "2026-11-11T13:25:27.090528+00:00",
  "original_issue": "KeyError('peak_concurrent_cases')",
  "correct_field": "actual_peak_concurrent_cases",
  "completed_full_runner_preserved": true,
  "completed_full_runner_exit_code": 0,
  "completed_full_run_dir": "/home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/full/run-20261111T125152Z-mzkzp4lk",
  "solver_source_or_binary_modified": false,
  "experiment_restarted": false
}
```

## 复现与关键SHA

仅重算本报告（不启动求解；需可读取源输入与snapshot的node3环境）：

```bash
python3 -B /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/code/analyze_ablation.py --campaign /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60
```

单元测试使用临时合成数据，不启动求解：

```bash
python3 -B -m unittest discover -s /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/code -p test_analyze_ablation.py
```

原实验的每组精确命令和环境保存在各组launch.json；runner是snapshot/scripts/run_ufnia_experiments.py，独立auditor是snapshot/scripts/summarize_ufnia_experiment.py。复跑实验会启动求解，须另行安排；报告命令仅分析。

| 资产 | SHA256 |
| --- | --- |
| core二进制 | a6e8de9a91586211ec96d3f068ac2c759f4b0853c0592a8bf77b2cf7a38743d8 |
| runner | 28dca3b10456f2c4f7a6a9d0bee50a353c1747fad58bc4a3eac2891d681b79f7 |
| 源码main.cpp | 12eebbebf3a854c20d773adfa5a09d78ed8aaee51c4526ecf9053e672cd8844f |
| 源码nia_affine.cpp | c4203a3717d9d16292d67f404efc52af8acebb19e68af4d029c3aae0931c40bf |
| code/analyze_ablation.py | 4972fb2a756090b52b95c6ca6e5d80043f9ef75b4a6c9b068280b4e36797f074 |
| code/test_analyze_ablation.py | 412e600385adfc1c2bcdda76b1bebcc069486d8d072504718a4786e183afd77f |
| harness_recovery.json | e877a9d134990b36b49bc30af21e1e2ba8e4364d2f5c5560ea01944f932af111 |
| mechanism_observations.json | 7ddda7f573f23c74cddb3b5dc993afa3fe2f430115c75a99c5c7d34372555d71 |
| root_fresh_verification.json | aee9e9dc7fbdcca9479e7e2e73f14411749a30a22d7580b47f04babec0b0ead7 |
| campaign_config.json | 8d8bbd8118679d2e78c5cedd763518390a34452fb7a43ef4c2c1504d0a36cc26 |
| campaign_status.json | 7227b825804214fac5b3fc277bdae641a362dee3ebe7e09ec4bc815292aea8e4 |
| inputs_sha256.json | 5cda91a72775b058301cbb53c981fe76cb688c6b20c42055cbb168469df623d0 |
| input_manifest.json | e7b90c4365afa03f635775024eaaa0e87f0bb9eb91ae5954875de753da72994e |
| memory_profile.csv | 5fb9f9de20da687e784f68011eebcccf5ed81a7b2df84db852ca0a21edb2777a |
| snapshot.json | 17c4b79ec5594ec616722694bbe22dfb9e1d8d9c4df2c681f24e2b7fe26259a7 |

完整源码、二进制与依赖SHA见run.json的source_sha256/binary_sha256、snapshot/snapshot.json及external_dependencies.json；输入逐例SHA见inputs_sha256.json。报告所读audit/detail/flag与run四件证据SHA保存在statistics.json的fingerprints。

产物：summary.csv（分family及TOTAL）、detailed.csv（572例三组结果、verified/time/PAR2及共同SAT耗时比）、comparisons.csv、sat_changes.csv、statistics.json、REPORT.md。

原实验复现命令如下（仅供记录，本次未执行）；输出目录需替换为新的空目录。输入manifest、profile与snapshot保持上述SHA。

```bash
env MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 UFNIA_AFFINE_FOOTPRINT_CACHE=1 UFNIA_AFFINE_PROGRESS=1 UFNIA_AFFINE_VIEWS=1 /usr/bin/python3.12 -B /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot/scripts/run_ufnia_experiments.py --solver-root /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot --benchmarks /data/shared/lbh/QF_UFNIA/sat/QF_UFNIA --output /path/to/new_ablation/full --require-host node3 --jobs 180 --timeout 600 --limit 0 --manifest /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/input_manifest.json --memory-profile /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/memory_profile.csv --memory-reserve-gb 24 --order memory --solver-args '["--backend", "mature", "--uf-observers", "--interaction", "joint", "--scoring", "paws", "--seed", "0", "--preprocess-timeout", "600.0"]'
env MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 UFNIA_AFFINE_FOOTPRINT_CACHE=0 UFNIA_AFFINE_PROGRESS=1 UFNIA_AFFINE_VIEWS=1 /usr/bin/python3.12 -B /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot/scripts/run_ufnia_experiments.py --solver-root /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot --benchmarks /data/shared/lbh/QF_UFNIA/sat/QF_UFNIA --output /path/to/new_ablation/no_cache --require-host node3 --jobs 180 --timeout 600 --limit 0 --manifest /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/input_manifest.json --memory-profile /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/memory_profile.csv --memory-reserve-gb 24 --order memory --solver-args '["--backend", "mature", "--uf-observers", "--interaction", "joint", "--scoring", "paws", "--seed", "0", "--preprocess-timeout", "600.0"]'
env MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 UFNIA_AFFINE_FOOTPRINT_CACHE=0 UFNIA_AFFINE_PROGRESS=1 UFNIA_AFFINE_VIEWS=0 /usr/bin/python3.12 -B /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot/scripts/run_ufnia_experiments.py --solver-root /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/snapshot --benchmarks /data/shared/lbh/QF_UFNIA/sat/QF_UFNIA --output /path/to/new_ablation/no_affine --require-host node3 --jobs 180 --timeout 600 --limit 0 --manifest /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/input_manifest.json --memory-profile /home/lbh/smt/ufnia/result/ablation_full_qf_ufnia_20261111_204924_k1cf7a60/memory_profile.csv --memory-reserve-gb 24 --order memory --solver-args '["--backend", "mature", "--uf-observers", "--interaction", "joint", "--scoring", "paws", "--seed", "0", "--preprocess-timeout", "600.0"]'
```


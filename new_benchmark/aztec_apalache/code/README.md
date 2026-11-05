# Aztec / Apalache SAT QF_UFNIA

最终 SMT2 位于 instance/，生成程序、依赖清单和说明位于 code/。默认生成 6 个 SAT 实例：4 个达到信号法定人数的实例、2 个执行提案的实例。每个文件包含一个完整 QF_UFNIA 查询，status 为 sat。

## 生成与复现

从 node2 的仓库根目录运行：

~~~bash
cd /home/lbh/smt/ufnia
python3.12 -B new_benchmark/aztec_apalache/code/generate.py
~~~

生成依赖 Python 3.12、requirements.txt 固定的 z3-solver 4.16.0，以及 code/deps 中固定的 Aztec 源归档；当前环境均已就绪。恢复 Python 依赖可执行：

~~~bash
python3.12 -m pip install -r new_benchmark/aztec_apalache/code/requirements.txt
~~~

列出配置、单独生成或在临时目录保留完整证书：

~~~bash
python3.12 -B new_benchmark/aztec_apalache/code/generate.py --list

python3.12 -B new_benchmark/aztec_apalache/code/generate.py --case sp_quorum_k24

python3.12 -B new_benchmark/aztec_apalache/code/generate.py \
  --output /tmp/apalache-sat-review \
  --proof-dir /tmp/apalache-sat-proofs
~~~

默认选择 sp_quorum_k12、k16、k24、k32，以及 sp_executed_k12、k16。更多大小和一个带 veto 的配置可通过 --case 选择。单个配置名称均需带完整前缀，例如 sp_quorum_k16。已有同名同内容公式保留原文件；内容不同会拒绝覆盖，包括并发发布的情况。额外输出位置与证书目录限定在 /tmp，运行记录不会在家族根目录增加 raw、metadata 或 validation 文件夹。

## 公式来源与范围

这些实例的 classification 是 application_derived：slashing_model.py 对固定 Aztec/Quint 源模型进行的 Python/Z3 语义移植和有限轨迹 UF 编码。源仓库为 konnov/aztec-governance-formal-verification-2025q3，commit 为 6fee2e4f6dbbe632cf62b411e344e1a008642cd7。生成前核验源归档和七个相关源文件的 SHA256。

源模型及对应关系：

| 内容 | 固定源位置 |
| --- | --- |
| 空存储初始化与有限地址域 | spec/slashing_proposer_machine.qnt:104–160 |
| 成功 signal / submit 等动作 | spec/slashing_proposer_machine.qnt:162–240 |
| 用于寻找有趣状态的 false invariants | spec/slashing_proposer_machine.qnt:280–310 |
| round、信号计数、严格 winner 更新规则 | spec/empire_base.qnt:22–87 |
| submit 窗口、quorum、executed 条件 | spec/empire_base.qnt:91–147 |
| slasher 回调与 veto 检查 | spec/slashing_proposer.qnt:44–63；spec/slasher.qnt:30–48 |
| 配置边界与默认存储值 | spec/invariant.qnt:887–916；spec/types.qnt:167–173 |

每个实例采用选定的成功动作序列，从源码的空映射初态出发。quorum 配置执行 K 次 signal 并要求某个轮次/载荷达到 quorum；executed 配置再执行一次 submit。构造参数附加 Q >= K 的输入限制，R/Q/D/L、slot、轮次别名和载荷选择仍为符号变量。所有地址运算在本投影中均为相等和映射索引，使用有限源码地址集合的单射整数编码。源码允许动作之间全局 slot 下降，本模型保留这一点，只要求对应轮次上的成功 signal 守卫。

七个正元 UF 表示实际存储，包括 signal_count、last_signal_slot、winner、executed、round/payload presence 与 vetoed。对所有操作和目标可能访问的符号轮次键、ZERO 和三个载荷实例化完整读写及 frame 方程；相等键通过 UF 同余共享更新。未观察键可从空存储和同一动作序列扩展，不影响已观察值。

原有配置边界保留，包括 0 < R <= 10000、Q > 1、2Q > R、Q <= R、0 <= D < L、相关 Uint256 / Uint32 上界和 submit 目标轮次 <= 1000。有限 K 次信号的 quorum 见证本身蕴含 Q <= K，进而 R < 2K，因此实际搜索范围可能比声明边界小。整数使用精确数学语义；这些公式不是生产 EVM 字节码等价证明，也不覆盖全部协议动作序列。

## SAT 认证

发布前依次执行：

1. 严格 typed-AST 审计，检查实际 Core/Int/UF 运算，并额外检查未使用声明中的 sort，拒绝数组、量词、Real、BV 等额外理论。轻量化简后须仍保留正元 UF 和符号非线性整数运算。
2. 序列化前后逐条核对所有原始 assertion，避免漏条件或增加限制。
3. 用完整整数赋值及带默认分支的 total-UF 表代入每条原始 assertion，要求全部化简为 true。
4. 使用独立于 SMT 约束和 UF 表的 Python 字典执行源码级动作回放，检查配置、守卫、更新和最终目标。
5. 全部通过后才加入 status sat 并原子发布。

具体证书值只存在于 /tmp 的验证材料中，不作为约束写进 SMT2。生成与认证不调用 Solver.check；外部求解器计时独立进行。默认临时证书在生成结束后清理；显式 --proof-dir 可保留证书、审计和回放结果。

## 实测难度

2026-11-05 在 node2 测量：每个进程单线程、地址空间 4 GiB、墙钟限时 30 秒，最多同时运行 3 个求解进程。使用 Z3 5.1.0、cvc5 1.4.2、Yices 2.7.0。Z3 参数为 -smt2 parallel.enable=false smt.threads=1，cvc5 为 --lang=smt2，Yices 为 --mcsat --nthreads=0。时间包含启动与读取公式；>30 表示超时。

| 实例 | 字节 | assertion 数 | Z3 秒 | cvc5 秒 | Yices 秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| aztec_sp_quorum_k12.smt2 | 409700 | 2172 | 6.144 | 2.287 | 21.768 |
| aztec_sp_quorum_k16.smt2 | 711596 | 3652 | >30 | 5.380 | 3.333 |
| aztec_sp_quorum_k24.smt2 | 1564124 | 7764 | >30 | 12.033 | >30 |
| aztec_sp_quorum_k32.smt2 | 2748300 | 13412 | >30 | >30 | >30 |
| aztec_sp_executed_k12.smt2 | 457496 | 2509 | 3.234 | 3.253 | 6.559 |
| aztec_sp_executed_k16.smt2 | 774432 | 4085 | >30 | 12.936 | 10.839 |

超时实例的 SAT 性由上述完整证书证明。此处仅测量 30 秒预算，尚未测量 600 秒预算或 UFNIA_LS 性能。

## 原生 Apalache 对照

原生小模型确实找到了 signal_quorum_ex 的 SAT 轨迹，但其 oopsla19 SMT 导出不含正元 UF。实际 funArrays 日志含 32 处 Array sort 使用，并且该次原生运行失败。因此本目录的最终 UFNIA 实例采用上面的源码派生编码；没有删除原生数组或困难约束后把它们称作原生导出。

pilot.py 保留原全模型 governance_proposer_safety / init_gp / max-steps=0 入口；native_campaign.py 支持较小的源码见证模型和受限构造参数配置。它们将完整原生记录放在 /tmp，并按真实 outcome 记录 SAT、unknown、error，不能仅凭退出码 0 判断结果。

~~~bash
python3.12 -B new_benchmark/aztec_apalache/code/native_campaign.py \
  --runtime /tmp/apalache-native-runtime \
  --output /tmp/apalache-native-q2 \
  --profile sp_quorum_q2_d2 \
  --timeout 360 --smt-timeout 60
~~~

## 后端依赖与测试

Apalache 0.62.4 后端包已完整下载（192038403 字节），发布者 SHA256 校验及原生 parse 启动检查通过。依赖版本与哈希在 dependencies.json，归档位于 code/deps。已有完整包时下面命令只做本地校验；缺失时通过官方 API 获取一次临时地址，再复用现有固定 1 MiB 分片续传，不需要 sudo。

~~~bash
python3.12 -u new_benchmark/aztec_apalache/code/resume_backend.py --proxy-mode direct
~~~

原生运行器会通过 npm ci 安装锁定的 Quint 0.33.1 及依赖；Node 与 Aztec 源压缩包已经保留，Java 使用 PATH 中的安装。原生导出、模型、证书和日志均在 /tmp。固定 Aztec、Apalache 许可证保留于 code/。

全部 64 项家族回归测试已通过，可使用以下命令复查：

~~~bash
python3.12 -B -m unittest discover -s new_benchmark/aztec_apalache/code/tests -v
~~~

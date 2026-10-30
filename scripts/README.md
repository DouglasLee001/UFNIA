# QF_UFNIA 预实验

默认 benchmark 目录：`/data/shared/lbh/QF_UFNIA/non-incremental/QF_UFNIA`，递归读取全部 `.smt2`（目前 806 个）。每个输入启动一个独立求解器进程，最多 180 个同时运行。每题默认超时 600 秒。

在 scripts 目录运行四个求解器（每条命令分别完成一轮实验）：

```bash
cd /home/lbh/smt/ufnia/scripts
./run.sh yices2
./run.sh cvc5
./run.sh z3
./run.sh smtinterpol
```

自定义单题时限：

```bash
TIMEOUT=300 ./run.sh z3
```

先挑排序后的 10 个输入运行：

```bash
LIMIT=10 ./run.sh z3
```

可调整 `JOBS`（默认180）、`TIMEOUT`（每题秒数，支持小数，默认600）、`LIMIT`（默认0=全部）、`BENCHMARK_DIR`（输入根目录）、`RESULTS_DIR`（各solver目录的共同父目录，默认项目根results）。脚本不依赖当前工作目录，只依赖 Bash、Python3 标准库以及已编译求解器。

一次运行的结果保存为：

```text
/home/lbh/smt/ufnia/results/{solver}/
  latest -> runs/{本次运行ID}
  runs/{本次运行ID}/
    run.json
    results.csv
    summary.json
    logs/{benchmark相对路径}.stdout
    logs/{benchmark相对路径}.stderr
```

重复运行会保留历史目录；`latest` 指向最近启动的一轮。文件名相同但输入子目录不同的题目有独立日志。每题结束后 CSV 立即刷新。

`results.csv` 包含 `benchmark`、`result`、`wall_time_seconds`、`exit_code`、stdout/stderr 的相对路径和基础错误信息。`result` 为 sat、unsat、unknown、timeout、error 或 interrupted。非零求解器退出码或 stdout 中的 SMT-LIB `(error ...)` 响应优先计为 error，即使同一输出中包含 sat；timeout/interrupted 保留实际终止进程的退出码。

`run.json` 记录求解器命令/发布元数据、输入目录、并发、时限、选择数量和运行状态；`summary.json` 记录各类数量、总墙钟耗时、未运行数量和退出码。单题 wall_time_seconds 是进程启动至输出收集及清理的墙钟时间；180并行时会受系统负载影响。

Ctrl+C 或 SIGTERM 会停止派发新题，终止正在运行的求解器进程组及其子孙，保存已完成结果和 interrupted 记录；退出码分别为130/143。普通求解器 error、unknown、timeout 是实验结果，整轮仍可退出0；进程启动等基础错误使整轮非零退出。

SMTInterpol 默认使用 `competitors/smtinterpol/runtime/bin/java`，不依赖 PATH 中的 Java。需要指定其他运行时时，可设置 `JAVA_BIN=/absolute/path/to/java`，或 `JAVA_HOME=/absolute/path/to/jvm`；JAVA_BIN 优先，错误的显式路径直接报错。开跑前使用与正式求解相同的 JVM 环境执行 `-version` 预检（15秒上限）；失败时退出2，不创建新结果目录，也不改动已有 latest。版本输出和 JVM 参数记录在run.json的 `smtinterpol_preflight` 中。

SMTInterpol 默认每个 JVM 设置单CPU可见性、SerialGC及256MiB最大堆，避免180个JVM各自按整台机器配置线程和堆。可用 `SMTINTERPOL_HEAP_MB=512 ./run.sh smtinterpol` 修改堆上限；具体设置会记录在run.json。达到堆上限等求解器错误会记录在stderr及CSV。SMTInterpol 对真正的非线性输入可能返回unknown。

查看结果：

```bash
cat ../results/z3/latest/summary.json
head ../results/z3/latest/results.csv
```

检查脚本：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

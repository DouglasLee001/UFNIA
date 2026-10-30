# SMT(UFNIA) local search

`NIA_LS/` 是参考源码。本目录的求解器使用 GMP 任意精度整数，扩展参考代码中已有的精确整数搜索组件，接受 SMT-LIB 的 QF_UFNIA 输入。Z3 仅用于解析、预处理、模型转换和求值；实际搜索由自写的 GMP 局部搜索核心执行。

## 使用

在项目根目录：

```bash
make -C UFNIA_LS
./run.sh INPUT.smt2 --parse-only --preprocess-timeout 600 --json-report result/input-check.json
./run.sh INPUT.smt2 --seconds 600 --preprocess-timeout 600 --seed 0 \
  --model-output result/model.smt2 --keep-dir result/ufnia-intermediate
```

`./UFNIA_LS/run.sh` 是同一入口，可从任意工作目录调用。`--parse-only` 也会完成 UF/算术转换，并让实际编译的 GMP 核心解析中间文件；输出 JSON，不进行局部搜索。默认求解模式为每个 `check-sat` 输出 `sat` 或 `unknown`。局部搜索没有找到模型时返回 `unknown`，不把搜索失败当成 UNSAT 证明。

`--seconds` 是每个查询的 SLS/同余精炼预算；初次前端的 Z3 tactic 时限由 `--preprocess-timeout` 控制，原生遍历、文件序列化和核心读入另计。最大实例的完整转换实测需要数分钟。`--query N` 可选择增量脚本中的第 N 个查询。用户指定的验收目录为 `/data/shared/lbh/QF_UFNIA/non-incremental/QF_UFNIA`。

## UF 和大整数

所有常数、系数、赋值、乘积和二次根候选使用 GMP；不再经过 `atoll`、int64/int128 截断或浮点边界推导。Python 前端也解除十进制字符串转换的固定默认位数上限。新核心直接保留一般多项式及共享 AST；搜索中无法生成解析候选的高次项仍可使用随机移动，不会因次数高而被丢弃。

UF 抽象、支持集检查和中间文件序列化使用原生 DAG 遍历；序列化在一个共享 `let` 图中保留所有断言，并将变量规范重命名。辅助库与 Z3Py 使用同一个 Z3 4.16 动态库，加载时核验版本及函数地址，避免和独立核心的系统 Z3 库混用。

UF 应用按声明和实参保留。规模小时提前生成同余约束；规模大时使用惰性同余：先保存完整的函数声明/应用表，把应用值作为搜索变量，检查候选赋值中相同实参是否得到相同结果。发现冲突就加入对应约束，再搜索。最大实例的应用对有百亿数量级，因此不能一次性展开全部对。

惰性模式下，**normalized SMT-LIB 和对应的 metadata.json/UF 应用表共同构成中间表示**。单独拿 normalized 文件交给其他求解器，只能求解抽象公式；应通过本入口执行 UF 检查及精炼。`--keep-dir` 保留这两部分、核心日志、候选模型与查询报告。

整数 `div/mod` 保留负除数及除零的 SMT 语义；不会假定除零结果为 0。`pow2`、`intand` 等名字按输入声明处理，不擅自赋予额外语义。仅以等式使用的未解释 carrier sort 用独立的整数代表编码，包括 TwoSquares 中的 sort。

## SAT 验证与模型

核心在返回 SAT 前，用 GMP 精确检查传给它的全部原始 NIA 断言。前端随后恢复消去的变量、构造有限 UF 解释，并检查源查询的断言。两个门槛均通过才输出 `sat`；不一致的 UF 模型会触发同余精炼。

未解释 carrier sort 的 SAT 结论通过等可满足的整数编码获得；当前输出的是编码后的模型，JSON 明确标注 `validation_scope=encoded_carrier_assertions` 和 `original_model_validated=false`。Int/Bool 源查询则标为 `original_assertions`。读入覆盖率、SAT 成功率及模型输出范围分别记录。

## 构建和验证

依赖 g++ C++17、GMP/GMPXX 和系统 Z3 开发库。二进制为 `build/ufnia_ls/ufnia_core`。参考 `NIA_LS` 源码和源 benchmark 不会被改写。

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
python3 -m unittest discover -s UFNIA_LS/tests -p 'test_*.py' -v
make -C UFNIA_LS test
```

完整源文件解析审计在 `result/ufnia_upgrade/session_parse/`；预处理、UF 表检查和实际 GMP 核心逐例读入验收在 `result/ufnia_upgrade/full_corpus/`。这些读入检查不代表已经对全目录完成 600 秒求解实验。

本次最终验收：**806/806 通过，0 失败**，所有源文件 SHA-256 与冻结清单一致。包含 alive 179、ic 292、两组 CLEARSY 19/27、两组 Certora 76/206，以及 TwoSquares 7。完整逐例结果为 `validation.csv`，汇总为 `summary.json`；验收工具和核心均记录 SHA-256，全部例子使用同一冻结版本。555 例使用提前同余，248 例使用惰性同余，3 例无需非零元 UF 抽象。

保留旧转换器接口：`python3 scripts/convert_ufnia.py INPUT -o OUTPUT --target nia_ls` 生成参考 NIA_LS 的有限精度原子格式；`--target ufnia` 生成新核心的任意精度中间文件及 UF 表。实际 SMT(UFNIA) 求解请使用本目录的统一入口。

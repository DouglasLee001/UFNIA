# UFNIA → NIA_LS

当前完整 SMT(UFNIA) 入口是项目根目录的 `./run.sh INPUT.smt2`，使用新的 GMP 核心，支持任意精度与惰性 UF 同余；见 [UFNIA_LS 使用说明](../UFNIA_LS/README.md)。新增 `--target ufnia` 可导出其任意精度中间文件及完整 UF 应用表。

下文保留的是 `--target nia_ls`（默认）的旧参考解析器接口，其 int64 限制不再适用于新求解器。

在项目根目录执行以下两步。`INPUT.smt2` 换成一个真实 benchmark 的路径，输出路径选择尚不存在的文件：

```bash
cd /home/lbh/smt/ufnia
python3 scripts/convert_ufnia.py INPUT.smt2 -o result/converted/example.nia
build/nia_ls/nia_ls result/converted/example.nia \
  --strategy=corewarm2 --initialize=off --restart-repair=off --native-seconds=600
```

`corewarm2` 使用现有求解器的宽算术配置。两个 `off` 参数使验收直接进入 NIA 局部搜索；默认 `auto` 会先运行额外的局部搜索初始化阶段。这些阶段也不是完整 Z3 SMT 求解。

## 转换产物

- `example.nia`：SLS 实际读取的原子表和 CNF 子句。它不是 SMT-LIB，也不是 DIMACS。
- `example.nia.json`：实际 Z3Py 版本、输入 SHA-256、转换耗时、原子/子句/变量数量、符号映射和建议的 SLS 参数。
- 可选 `--emit-smt2 result/converted/example.normalized.smt2`：导出与原生输入对应的 QF_NIA 公式，便于检查和单独交给 SMT 求解器验证。

```bash
python3 scripts/convert_ufnia.py INPUT.smt2 \
  -o result/converted/example.nia \
  --emit-smt2 result/converted/example.normalized.smt2 \
  --timeout 30 --ackermann-limit 100000
```

`--timeout` 是整个转换子进程的墙钟时限，包含导入、解析、预处理和写入临时文件。`--ackermann-limit` 是 UF 消去的一致性约束预算；超过预算而仍残留 UF 时，转换失败，不输出含 UF 的假成功文件。转换成功返回 0，输入不支持、超时或其他转换错误返回 2。已有输出文件不会被覆盖；失败时不发布原生文件。源 benchmark 不会被改动。

## 支持范围与语义

依赖 Python 3 和 `z3-solver`，本次验证使用当前环境已安装的 Z3Py **4.16.0**。它与 `competitors/z3` 的 **5.1.0** 命令行版本分别记录；前端没有调用那个 CLI。其他环境可在自己的虚拟环境安装 `z3-solver==4.16.0.0`。

只接受无量词、断言中实际使用的类型均为 Int/Bool、单个无参数非增量 `check-sat` 的输入；Z3 解析器负责 `define-fun`、`let` 和转义符号。前端做化简、变量消元、算术 ITE 消除、整数 div/mod 净化、UF 的 Ackermann 消去以及 Tseitin CNF 转换，最后把长乘积拆成二元乘积定义。前端不调用完整 SMT 求解器。

整数 `div/mod` 保留负除数的欧几里得语义及除零时的函数一致性。名为 `pow2`、`intand` 或 `|*r|` 的 UF 保持其声明语义，前端不会根据名字擅自赋予指数、位运算或乘法语义。

原生解析器通过 `atoll` 读取常数和系数，所以输出必须在有符号 int64 范围内；比较式右端还不能等于 `-9223372036854775808`，因为原解析器在 int64 中对它取负会溢出。当前 Certora 的原始文件普遍含 256 位或更大的整数；若预处理后仍超范围，转换器明确报错。不会截断、钳制或把未支持表达式当作普通变量。断言中实际使用的量词、Real、位向量、数组、未解释 sort，以及递归定义和增量命令也明确拒绝。

转换成功表示生成了等可满足的、语法兼容的中间输入，不保证 SLS 能找到解。现有 SLS 本身使用有限精度、部分边界推导使用浮点数，且导出的模型可能缺少内部消去的变量；本次没有改造其数值核心或实现原始 UF 模型重建。`sat`/`unknown` 是 SLS 的搜索输出，不能把 `unknown` 当 `unsat`。若用于正式计分，还需独立验证原始公式的模型。

## 构建和检查

已编译二进制位于 `build/nia_ls/nia_ls`。原 `NIA_LS` 目录属于 root，采用原 Makefile 在可写目录编译，没有修改算法源码、Makefile 或权限。复现命令：

```bash
cd /home/lbh/smt/ufnia
mkdir -p build/nia_ls
make -C build/nia_ls -f "$PWD/NIA_LS/Makefile" \
  VPATH="$PWD/NIA_LS" \
  CXXFLAGS="-std=c++17 -Wall -g -w -O3 -I$PWD/NIA_LS" -j4
build/nia_ls/nia_ls --help
```

编译依赖 g++（C++17）、Z3 开发头文件和库、GMP/GMPXX；本次 C++ 二进制链接系统 Z3 **4.8.12**，这是 SLS 的 AST/模型求值依赖，不是 Python 前端版本。

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

测试独立解码原生格式并用 Z3 检查已知 SAT/UNSAT 用例，覆盖 UF 一致性、嵌套/布尔 UF、ITE、负数和零除模、高次乘积、恒真/恒假以及输入输出保护。Z3 完整求解只出现在测试 oracle 中。

实际构建日志和保留 family 路径的真实输入冒烟记录位于 `result/converter_validation/`。读入成功以实际 SLS 的 `search_result` 诊断为依据，单独记录 SAT、UNKNOWN、超时和错误；不会改写先前 `results/*/latest` 的预实验结果。

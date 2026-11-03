# VeriEQL：整数业务查询的有界等价性验证

## 交给 Codex 的目标

从公开 SQL 验证工程提取新的、可解释的 QF_UFNIA 查询。每个实例必须能对应到“数据库模式、两条 SQL、完整性约束、行数上界”这一组输入。优先选择整数列相乘的查询；不要向一个原本线性的查询随意添加无业务含义的 UF。

本说明是实施任务书。调研阶段核查了论文、仓库说明及部分 `environment.py`，没有运行 VeriEQL，也没有证实其全部查询属于 QF_UFNIA；以下 pilot 输入是建议新建的输入，并非声称来自论文原始测试集。

## 已核实材料与理论来源

- 论文：Yang He、Pinhan Zhao、Xinyu Wang、Yuepeng Wang，*VeriEQL: Bounded Equivalence Verification for Complex SQL Queries with Integrity Constraints*，OOPSLA 2024，DOI：<https://doi.org/10.1145/3649849>；作者预印本：<https://arxiv.org/abs/2403.03193>。
- 官方代码及 Formula writer 说明：<https://github.com/VeriEQL/VeriEQL>。仓库列有 Calcite、Literature、LeetCode 查询集合。README 推荐稳定 tag；实际执行须记录选用的完整 commit，不能只记录 `main`。
- 已查看的源码入口：<https://github.com/VeriEQL/VeriEQL/blob/main/environment.py>。其中 `VarSort` 使用 `IntSort`，列值可通过 `TupleSort -> Int` 的函数读取。`NULL` 等函数还使用 `StringSort` 的列标签，因此不能把所有输出直接标为 QF_UFNIA。

应用中的 NIA 来自数量乘单价、税基乘税率整数分子、按件计费等业务计算。例如 `qty(t) * price(t)` 同时包含两个符号整数列读取。金额采用整数分是一个清楚的输入建模选择；如果原查询采用小数或浮点数，不能仅为迁就目标逻辑而悄悄改成整数。

## 最小 pilot

先生成一个含 `order_id`、`quantity`、`unit_price` 的订单表，三列均采用受支持的整数类型。若使用非空、主键或非负约束，必须通过工具正式的输入接口加入，并保存原始输入。先在仓库测试和输入解析代码中确认约束格式，不要编造命令行参数。

建议先建立两个查询对，表的行数上界取 2、3、4：

```sql
-- 查询 A：订单金额
SELECT order_id, quantity * unit_price AS amount
FROM Orders WHERE quantity > 0;

-- 查询 B：用于检查导出管线的正确重写
SELECT order_id, unit_price * quantity AS amount
FROM Orders WHERE quantity > 0;

-- 查询 C：带有可解释错误的变体，每件多收一分钱
SELECT order_id, quantity * (unit_price + 1) AS amount
FROM Orders WHERE quantity > 0;
```

任务 A/B 用于预期无反例的检查，A/C 用于反例搜索。它们属于新构造的应用派生 pilot，不能当作真实生产查询原样导出。预期结果不是标签依据：必须实际求解并核验。接着从公开输入集合中筛选整数乘法查询，优先保留已有复杂连接、分组或完整性约束的真实输入，再尝试 `SUM(quantity * unit_price)` 类新性质。不要预设整个公开集合都含非线性。

## 实施与导出

1. 固定工具版本、Python/Z3 依赖和 SQL 方言；按该版本 README 运行最小安装检查。不要为性能擅自覆盖用户现有 Python 环境。
2. 跟踪一次完整的查询对验证。确认数据库约束、查询语义约束和“结果不同”的条件都已经提交。必须记录 bag/list 语义、NULL 语义以及数据库行数上界。
3. 优先复用 Formula writer；如得到的是 Z3 Python 脚本，在完整差异公式构建完毕、实际 `check` 之前加入 SMT2 导出。调用方式以锁定版本源码为准，不保证 README 中不同版本的初始化参数一致。
4. 对 `check(a1, ..., an)` 一类 assumptions，单独记录并纳入单查询快照；`to_smt2()` 通常不能代替对 assumptions 的处理。不能只导出基础数据库约束而漏掉反例条件。
5. 原始输出保留一份；任何字符串标签消除、声明清理或断言规范化都另存变换版，并记录对应关系。

增加行上界时，数据库的表示可能重新构建，不能假定不同上界天然形成可共享的增量上下文。只有捕获了真实求解调用序列，才能称为原生增量实例；自行设计的共享模式须标为派生增量工作负载。

## QF_UFNIA 筛选的特别要求

所有查询必须通过实际 AST 检查：无量词、无数组、无 Real/FP/BV/字符串运算或其他未允许理论。未解释的 `TupleSort` 可保留；`StringSort` 是解释理论的类型，不能通过修改 `set-logic` 忽略。

如果字符串只作为有限、固定、两两不同的列标签，且没有拼接、长度、排序或其他字符串语义，可研究将 `NULL(t, "quantity")` 变成每列专属的 `NULL_quantity(t)`。须验证标签集合确实有限、固定且无别名，并证明或检查变换保留所有可观察关系。若标签是符号值、可相等，或还有跨列约束，不能直接拆分。清理未用声明同样要以可达符号分析为依据。不能直接删除含字符串的约束。

固定且互异的行标识可能让所有列读取 UF 在预处理后退化为独立整数变量。这类实例仍可保留为应用查询，但应记录 `uf_interaction=weak_or_eliminated`，不要宣称它能检验深度 UF/NIA 交互。记录原始与预处理后的非零元 UF 应用数、符号参数数、非线性项数；“UF 存活”本身也不证明 UF 不可消除。

## 结果解释、去重与交付

- SAT 表示该有限数据库界内存在反例。将模型还原为具体数据库，在工具支持的真实 DBMS 中执行两条查询，按原有 NULL 与重复行语义比较结果。SQL 字符串不同不能当作结果不同。
- UNSAT 只说明给定行数上界和编码条件内没有反例，不得写成对任意规模数据库的全局等价证明。先确认原始导出与规范化版本一致，再进行独立求解核查；未确认的状态保持 `unknown`。
- 保存源 SQL/模式/约束、查询对 ID、行上界、方言、工具 commit、导出位置、变换日志、求解与回放结果。将“原始应用导出”“新查询/新性质”“应用派生 pilot”明确标识。
- 对 SMT-LIB 发布库和提交仓库做文件及结构去重；另按查询对 ID 去重，避免同一应用仅更换编码就被当作新应用。训练/调参集与测试集应按查询对或模式划分。
- 先交付一个可重跑 pilot，再扩展真实查询。若工具不支持某个 SQL 特性或无法保持语义地消除额外理论，记录排除原因，不要制造看似合格的 SMT2。

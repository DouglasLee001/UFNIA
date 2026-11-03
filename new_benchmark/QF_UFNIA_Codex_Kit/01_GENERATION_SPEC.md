# 生成工程总规范

## 1. 任务范围与证据等级

目标是新的真实应用查询及其可复现生成过程，不是随机公式生成。资料证据分为：已核实论文/文档/部分源码、实施建议、待实际运行验证。不要把本包建议写成原项目已经存在的功能。

主集优先使用 `faithful_export`：在不改变原应用语义的条件下记录实际查询。`application_derived` 允许按真实操作语义建立可解释模型，但必须单独标记其与真实实现的对应关系。`source_mutant` 应在原程序/规格/重写规则层做有意义的变更，保留 diff。`crafted_demo` 只用于说明和测试导出器。

## 2. 推荐工程结构

```text
generated-qf-ufnia/
  README.md
  REPORT.md
  campaign.yaml
  requirements.lock / flake.lock / 其他实际依赖锁定文件
  generators/
    aztec/
    tensorright/
    verieql/
    tac/
  tools/
    extract_queries.*
    check_logic.*
    normalize_and_dedup.*
    run_solvers.*
  upstream-locks/
  raw/
  benchmarks/
    non-incremental/QF_UFNIA/<family>/
    incremental/QF_UFNIA/<family>/
  metadata/
  manifests/instances.jsonl
  validation/
  dedup/
  excluded/
  reports/
```

不强制语言；优先沿用应用工程和依赖，避免重写已有编译器/符号执行器。提供单实例、pilot、campaign 三种入口和明确参数。大仓库或原始日志不必重复打包，但必须有固定 commit/URL、校验和和获取方法。

## 3. 逻辑成员与研究主集资格是两个问题

QF_UFNIA 使用 Core、整数和自由未解释 sort/function，且断言无量词。不要引入数组、位向量、实数、字符串理论、代数数据类型、递归函数或高阶函数。`let` 可用；自由常量不是量词变量，也不需要固定为具体数值。`declare-sort` 是自由 sort，不等于 datatype。

用锁定版本的合规解析器/类型检查器核查，而不是只跑文本 grep。递归展开普通 `define-fun` 或在类型环境中遍历 AST，检查实际使用的 sort、运算和函数应用；未使用的声明可清理，但不能删除有用约束。重定义命名、quoted symbols、字符串注释、let 绑定和注解都要正确解析。

整数的 `div/mod/abs` 不能简单按“不是乘法”全部排除。它们的语法归属和语义需按标准检查；其中符号分母的 div/mod 是重要候选。`abs`、常数乘法、或恒可折叠的乘积本身不说明存在有意义的非线性负载。为跨求解器兼容，固定非负幂展开为乘法；本包默认排除 `**`。2026 Ints 理论增加了 `**`，但官方 NIA/UFNIA 定义仍排除该运算（S17、S18）；不能看到理论中有运算就推断目标逻辑允许。

纯 NIA、纯 UF、仅线性 UF+Int 可能被宽逻辑接受，但不进入研究主集；保存到对照或排除目录，记录理由。主集至少应有：

- 可追踪到应用含义的、实际参与断言的正元 UF 应用；零元声明只是常量；
- 实际符号整数非线性或相应 div/mod 负载；
- 两类结构共同存在，优先选择共享整数项、参数相等性或控制条件发生联系的查询。

有限 ground UF 可做 Ackermann 化，不能以“UF 无法消除”作为要求。记录原始/轻量预处理后的应用数量、distinct 参数项、共享 Int 项、乘积和 div/mod 等特征；不把“存活”误报成数学上不可消除。

## 4. 语义保持

除法不是跨语言通用的：SMT Ints 是欧几里得除法；程序语言可能截断至零。只有已知 `d>0` 时可直接用 `a=q*d+r, 0<=r<d` 取代商余数。负分母按正确标准处理；零分母的总函数语义不能被偷偷替换成 `d>0`。应用本来禁止零分母时保留原约束和来源。

不能删量词以获得 QF。量词自由 phi 的普遍有效性可通过新自由常量上的 `not phi` 寻找反例，但含量词交替、公理或归纳关系的一般公式不能如此转成 QF。有限实例化、抽象和弱化须声明 `equisatisfiable / overapproximation / underapproximation / unknown` 的关系及论证；非等价结果单独发布，不与忠实主集合并。

机器整数必须保留回绕、溢出检查、revert、符号性和除法语义。仅加 `0<=v<2^256` 不够。SQL 的 NULL、bag 重复计数、连接、完整性约束也属于语义。TensorRight 的整数元素模型不能被描述为 IEEE 浮点等价。

## 5. 查询含义

每个文件说明是 `counterexample / reachability / witness / feasibility / other`。常见反例查询为 `precondition AND transition AND NOT(property)`：SAT 为存在反例，UNSAT 为当前编码范围内无反例；不能把两者写反。

BMC 深度、行数上界、循环展开界都须记录。归纳步骤、初始条件和完整系统结论分别表述。一个子 VC 的 UNSAT 不自动证明整个系统。

## 6. 输出格式

单查询文件只有一个 `check-sat` 或等价规范化后的查询，包含必要声明和完整当前条件；以 `exit` 结束。增量文件保留实际查询序列/作用域，并为每次查询保留状态、假设及索引元数据。详见 `02_VALIDATION_AND_NOVELTY.md`。

头部按 `templates/benchmark_header.smt2.in` 填充。`:status` 缺证据时为 `unknown`，不能照搬模板期待值；官方提交格式还应以所用 release 的要求为准。许可证来自实际权利和项目许可，不因模板而自动变成 CC BY。

每例一份 metadata；完整 manifest 每行一个实例。使用 `templates/manifest.schema.json` 校验。路径相对于输出根目录，文件的 SHA256 使用最终 bytes。运行日志里不要保留凭据或带令牌的链接。

## 7. 最小交付标准

至少一条真实应用路线形成可重跑闭环，且每个主集实例均通过逻辑检查、查询完整性核对、溯源、实际状态记录和固定快照去重。两条优先路线中若一条因环境不可用，交付另一条并记录阻塞，不伪造全覆盖。

同一个实例的不同导出方式或编码作为 paired variants 关联，不当作独立应用任务夸大数量。保留随机种子用于复现采样，但 solver seed 本身不产生新的基准公式。

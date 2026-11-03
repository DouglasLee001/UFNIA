# TensorRight / XLA 张量重写 → QF_UFNIA

## 任务目标与证据边界

从真实张量编译器优化规则的验证工程产生新查询，重点收集“非线性整数索引 + 张量 UF”发生交互的实例。优先整数元素、不含 reduction 的切片、填充与动态更新组合，再扩展其他规则。

截至 2026-10-31，已核查论文、官方仓库 README、构建配置列出的部分入口，以及 Grisette 后端 API 文档；本包未构建 TensorRight、未导出查询、未完成 SMT-LIB 去重。本文提出的扩展轴不是已完成的实验结果。

## 原始材料和版本固定

- 论文：Jai Arora 等，*TensorRight: Automated Verification of Tensor Graph Rewrites*，POPL 2025，DOI `10.1145/3704865`：<https://lsrcz.github.io/files/POPL25.pdf>
- 官方工程：<https://github.com/ADAPT-uiuc/TensorRight>
- 论文 artifact：<https://zenodo.org/records/14159871>
- Grisette 后端 API：<https://lsrcz.github.io/grisette/Grisette-Backend.html>

优先复现论文 artifact 或选择一个固定工程 commit；记录 Stack/GHC、Grisette、SBV、Z3/cvc5 版本和规则来源 XLA commit。论文和当前仓库的已支持/已验证规则数量可能不同，不要混用版本统计。代码遵守仓库许可，论文引用和生成查询的来源说明要随包保留。

## 为什么自然形成 UF + NIA

论文 §6.4 用从访问索引到元素的 UF 表示无界尺寸张量。固定 rank 后，一个整数张量可概念性地写成：

\[
  X:\mathbb Z^d\to\mathbb Z.
\]

索引、尺寸和操作属性是符号整数。以一维符号步长切片为例，访问结构是 `X(a + i*s)`：`a` 为起点，`i` 为输出索引，`s` 为步长。`i*s` 是 NIA，结果又进入 UF 参数。比较两种重写结果时，需要同时判断访问范围、索引算术关系和同函数同参数同结果；这不是随意向 NIA 公式加入一个无关 UF。

以上表达是语义结构说明，不是从该工程抽取的原始 SMT2。必须保留真实操作的维度、有效性、步长和越界语义。不要因为公式写了乘号就认定存在非线性：常数乘变量仍是线性的。

论文 Fig.11 的广义 `FoldConvInputPad` 还有明确符号属性乘积，例如：

\[
 S_{ol}=S_l+S_iS_{lp},\quad
 S_{oh}=S_h+S_iS_{hp},\quad
 S_{oi}=S_i+S_iS_{ip}.
\]

但卷积属于 reduction：论文 §6.5 为验证 reduction 的双射关系使用量词。这组规则只能作为后续筛选来源，不能将所有查询直接声称为 QF_UFNIA。论文版本不支持 reshape/bitcast 这类布局敏感规则，不要把它们写成既有可用功能。

## 最小 pilot 和代码入口

1. 在固定 commit 下按照 README 构建；先运行单个已知小规则，再考虑全套 `make verify`。保存完整命令、输出和资源限制。
2. 从实际规则目录筛选整数元素的 Slice/Pad/DynamicUpdateSlice 及合法组合；确保符号步长、偏移或相乘的尺寸没有全部被固定成常数。不要凭目录名称判断查询理论。
3. 先导出 5–20 个查询，允许输出排除项。先确认原始验证过程，再增加组合深度和分支数；不追求通过随机复杂化凑数量。

构建配置可作为定位线索：`src/TensorRight/Internal/Core/Verify.hs`、`Tensor.hs`、`Tensor/TensorInt.hs`；已知卷积/广义规则入口为 `rules/xla/conv/Main.hs`、`rules/xla/generalize/Main.hs`，可执行名称包括 `rules-xla-conv`、`rules-xla-generalize`。这些是已核对的模块/配置入口，文件内函数名和当前分支具体调用链仍需 Codex 读源码确认；切片入口不要在未读取前猜测。

先读验证器如何建立三个类别的条件：shape、访问范围、元素相等。shape/范围条件可能不含任何 UF，因此应单独分类；适合混合理论的主要对象是保留张量读取的元素条件。不要为了增加 UF 数量向纯算术 shape 查询添加无关约束。

## 如何导出 SMT 交互

没有核实 TensorRight 自带一键 `--dump-smt` 参数。官方 Grisette API 提供 `GrisetteSMTConfig.sbvConfig`，其 `SMTConfig.transcript :: Maybe FilePath` 可记录求解器交互。已读 API 是 0.13，而此前核查工程配置依赖 0.11；应先检查锁定版本的 API，必要时仅在配置或求解器封装处补记录钩子，并保存补丁。不要通过盲目升级依赖改变验证语义。

记录完整声明/定义、断言、scope、`check-sat`/`check-sat-assuming` 与选项。查询通常是性质的反例检查，但必须读代码确认极性；不能把要证明的命题直接 assert 后求 SAT，随后误标为反例。

单查询输出重建每次调用时的活跃断言和全部 assumptions；若日志存在 push/pop、局部声明或模型枚举的 blocking clause，都要保留对应上下文。增量版本只保留真实会话。不同 rank 或不同规则如果原来独立求解，应作为任务集合交付，而不是把它们串接伪装为应用原生增量调用。

## QF_UFNIA 筛选及状态证据

第一批只取整数元素和整数属性、无 reduction 的量词自由查询。允许目标逻辑支持的 Bool、Int、UF、未解释 sort；排除 Real、数组、BV、String 理论、代数数据类型、量词及超出目标理论的算子。残留 lambda/高阶项也必须处理并说明。`set-logic` 不是理论检查，必须检查实际 SMT AST 并做标准解析/类型校验。

检查整数除法、取余、负索引和步长的定义是否与上游一致。不要把 Int 换成 Real 再求解；不要仅删量词、删除 reduction 公理或把有含义的运算都改成任意 UF。需要语义变换时记录是等价、等可满足还是单向近似，并给出适用条件；近似查询不能冒充原始验证任务。

SAT 应能重建一个合法的 shape、属性、访问索引和必要的张量元素，使原规则在相应语义下失败；对有限访问部分可具体回放，无须枚举整张无界张量。UNSAT 对应某个固定 rank 的验证子条件成立，不能单独声称整个任意 rank 规则已证明；完整规则结论还依赖所有子条件、rank 界定方法及 reduction 相关证明义务。超时保持 unknown。

## 怎样得到有意义的新实例

按以下顺序扩展：先忠实导出现有工程；再从新的 XLA commit 移植实际规则；然后建立 Slice/Pad/Reverse/DynamicUpdateSlice 的合法组合规则，或考察实际优化前提是否可弱化。保留符号步长、不同轴的形状关系及算术索引别名，是值得尝试的规模轴；每个轴都应有具体语义说明。

原始导出标 `faithful_export`；自行建立的新组合模型标 `application_derived`；对已有规则弱化前提或引入故障的源层变体标 `source_mutant`，保存 diff，明确改动及动机，不称其为生产 XLA 已存在的 bug。正确和错误变体须由验证证据定状态，不能根据“预期正确”写死 unsat。限制整数大小或固定 rank 只构成对应子任务，不应替代原始任务。

统计预处理前后 UF、符号乘法、div/mod、布尔分支、共享参数和一致性相关应用。UF 均可有限消除，“仍有 UF”不等于不可消除或一定困难；需要测量实际结构和求解表现。对已有 SMT-LIB 与 staging 做原始/规范化去重，并按原始规则或工程划分实验数据，避免同一重写的不同 rank/编码造成泄漏。

交付固定源码、运行与导出脚本、必要补丁、查询元数据、SAT 回放或 UNSAT 证据、排除清单和去重结果。若某入口依赖不可获得、导出 API 不兼容或所有候选被理论筛选排除，给出可复现的阻塞记录，不能用手写 `X(a+i*s)` 小例子替代而不说明。

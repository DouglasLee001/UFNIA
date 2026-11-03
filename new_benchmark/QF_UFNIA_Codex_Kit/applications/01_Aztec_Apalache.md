# Aztec 治理协议 → Apalache → QF_UFNIA

## 任务目标与证据边界

从真实 Aztec 治理协议的公开 Quint 规格重新运行验证，导出包含 UF 与非线性整数约束的 SMT 查询。优先保留实际验证流程，形成单查询和增量两种交付。不要把本文里的结构示例直接复制扩充成“工业基准”。

截至 2026-10-31，已核查作者报告、仓库 README、Apalache 的 tuning 和 outputs 文档；本包没有实际运行该工程，也没有完成与 SMT-LIB 全库的比对。作者报告中的 125 个不变量、992 个验证任务不是已经确认的 QF_UFNIA 实例数量。

## 原始材料

- 规格、复现实验脚本、报告及源码子模块：<https://github.com/konnov/aztec-governance-formal-verification-2025q3>
- 作者报告，重点看 §7.3、§7.4、§8：<https://protocols-made-fun.com/aztec-governance.html>
- Apalache 后端和 QF_UFNIA 配置：<https://apalache-mc.org/docs/apalache/tuning.html>
- Apalache 输出设计，列出 `log0.smt`：<https://apalache-mc.org/docs/adr/009adr-outputs.html>
- 整数算术含义：<https://apalache-mc.org/docs/lang/integers.html>

先克隆并固定工程 commit，初始化所需子模块；记录 Aztec 源码子模块 commit。安装方式、Java/Node/Quint/Apalache 版本以固定版本工程为准，保存锁文件或容器摘要。不要用浮动 `latest` 运行完后声称实验可复现。公开规格验证的是协议级模型；它不自动等价于当前生产 EVM 字节码。

## UF 和 NIA 的真实来源

规格包含合约地址到状态、rollup 到轮次、轮次到记账状态等映射。Apalache 的 UF 编码可承担这些结构的表示；具体 SMT 函数名、签名和应用位置必须从实际日志确认。高层的 Quint 函数不一定成为 SMT UF，许多函数会先内联。

作者指出投票计算产生 NIA，并给出 GovernanceProposer 信号约束，其结构为：

\[
  c \le s \bmod R+1,
\]

其中 `R = ROUND_SIZE` 来自构造函数，`s` 是记录的 slot，`c` 是信号计数。若 `R>0`，可用整数商余关系理解其非线性：

\[
  s=qR+r,\quad 0\le r<R.
\]

这里 `qR` 是变量乘积。它仅是解释性表达，不是已经导出的原始公式。不能固定全部配置常数之后，还把已退化为线性算术的公式当作主要 NIA 样本。也不要自行用这组约束替代原日志：整数除法、负值、零除数的语义都必须符合上游实际约束。

## 先完成一个小规模 pilot

1. 通读固定 commit 的 README、Makefile、`scripts/`，检查 `spec/invariant_model.qnt` 中的初始化谓词、转移和不变量定义。
2. 先做 typecheck，再运行一个可终止的小验证任务。仓库 README 中的入口示例为：

   ```bash
   quint verify --max-steps=0 --init=init_gp \
     --invariant=governance_proposer_safety spec/invariant_model.qnt
   ```

   该命令是已核实的 README 示例，仍需以固定版本 `--help` 校验。这里的初始化可能描述任意满足归纳假设的状态；不得把 `--max-steps=0` 简单解释为“从空部署状态完整验证了协议”。
3. 核查 `scripts/quint-inductive.sh`，只选择少量“转移 × 验证条件”组合。记录拆分前后条件之间的关系。不要一开始就同时运行 992 项重任务。
4. 优先提取实际包含符号轮次配置、投票计算或映射读取的查询。pilot 目标是取得 5–20 个可回放查询，不要求全部满足目标理论；不合格者在报告中写明排除原因。

## 忠实导出与实现位置

确认所用 Quint 如何调用 Apalache、是否生成 TLA+ 中间文件、SMT 日志在哪里产生。ADR 中的 `log0.smt` 是已知线索，不保证所有版本无条件输出完整日志。若缺日志，在 Apalache 求解器封装层记录真实发送的 SMT-LIB 请求，或启用该固定版本提供的记录能力。需要补钩子时把补丁随生成器交付。

Apalache 文档指出默认 `oopsla19`/`funArrays` 可采用 UF 算术编码，`arrays` 使用数组理论；CVC5 非线性配置示例是 `cvc5.smt.logic=QF_UFNIA`。必须确认配置的传递方式，不能未经核实把 Apalache 参数直接拼给 Quint。改变编码或后端应标为独立变体。

完整记录声明、定义、断言、`push`/`pop`、`check-sat`、`check-sat-assuming`、选项及调用顺序。对每次查询保存活跃断言和 assumptions；不要只导出最后的 solver assertions，更不能漏掉本次查询的假设文字。日志若带求解器响应，将响应转换为注释或单独日志，不能当作 SMT 命令。

单查询快照应重建对应时刻的活跃作用域、必要声明/定义和本次 assumptions，然后检查一次。增量版本保留原始调用序列，不要把相互独立的任务拼成伪造的增量会话。注意局部声明与标准选项 `:global-declarations` 的差异；不要依赖求解器专属别名。

## 理论筛选和状态解释

对展开后的实际语法检查：允许 Bool、Int、UF 和目标逻辑允许的未解释 sort；排除量词、数组、Real、BV、String 操作、代数数据类型等额外理论。`set-logic QF_UFNIA` 只是声明，不是合格证明。用标准 SMT-LIB 解析/类型检查工具做复核。

另做结构统计：保留的正元 UF 数、应用数、共享整数项、符号乘法、非恒定除数的 div/mod、布尔分支及预处理后的存活情况。UF 可以有限地 Ackermann 化；“UF 存活”也不等于它不可消除。优先保留存在算术索引别名和一致性交互的任务，避免只有无关 UF 装饰的样本。

验证条件的反例查询通常是“假设 ∧ 转移 ∧ 违反性质”：SAT 表示当前模型/假设下存在反例，UNSAT 表示该验证条件没有反例。SAT 的归纳状态不一定可从真实初始状态到达。可达性 witness 查询的 SAT 则有不同含义，必须在元数据中区分。求解器超时记为 `unknown`，不能因为原作者没有发现错误就标 `unsat`。

SAT 模型应回放到规格变量并核对原性质；可达 witness 要检查完整轨迹。UNSAT 优先采用可检查证明，或独立求解器复核并注明证据强度。模型回放和求解器一致不是对协议实现的额外正确性证明。

## 如何扩展成新任务

先做忠实导出，再考虑真实配置范围、合约实例/参与者数、映射域规模、不同性质、转移组合和协议源代码版本。保持源码中的合法配置约束。自行建立新模型或组合任务标为 `application_derived`；对原规格、性质或前提做源层变更标为 `source_mutant`，保留 diff 和动机；原始导出标为 `faithful_export`。限制整数域属于新的有界变体，不能替代原任务的结论。

对官方 SMT-LIB 发布库、增量快照和 2026 staging 做原始 hash 与规范化结构去重，同时核查应用 provenance。改名、改注释、改变求解器种子不能构成新应用任务。训练/测试应按原始性质或工程划分，避免同一 VC 的不同编码落入两侧。

最终交付源工程固定版本、运行命令、导出补丁、查询及元数据、状态证据、排除清单和去重报告。若工具版本、依赖、日志导出或理论筛选阻塞，应如实写入阻塞报告；不得用手写模拟数据顶替而不说明。

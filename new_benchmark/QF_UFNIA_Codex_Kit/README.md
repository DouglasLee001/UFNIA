# 新真实应用 QF_UFNIA 实例：Codex 交接包

版本：1.0；整理日期：2026-10-31（Asia/Shanghai）。

## 怎么交给 Codex

将整个压缩包解压到 Codex 工作目录，让 Codex 先读 `00_START_HERE.md` 并执行。该文件可以直接复制为第一条任务提示词。不要只给它某个应用名称：本包中的理论筛选、查询提取、语义和去重要求是任务的一部分。

本包交付的是**生成器与基准集的实施说明**，不是已经跑通的生成器，也不是已完成去重的新 SMT2 数据集。本轮核查覆盖论文、官方文档、项目 README 和部分源码；没有运行四个应用的完整导出链。没有把手写示例混成应用基准。

## 阅读顺序

1. `00_START_HERE.md`：完整任务指令。
2. `01_GENERATION_SPEC.md`：目标、输出结构、理论与语义约束。
3. `applications/01_Aztec_Apalache.md` 与 `applications/02_TensorRight_XLA.md`：第一批优先来源。
4. `applications/03_VeriEQL_SQL.md` 与 `applications/04_Certora_TAC.md`：补充来源。
5. `02_VALIDATION_AND_NOVELTY.md`：状态验证、增量快照、SMT-LIB 去重。
6. `03_EXECUTION_PLAN.md`、`checklists/ACCEPTANCE.md`、`templates/`：执行与验收。
7. `SOURCES.md`、`sources.json`：可追踪来源及其证据边界。

## 四条路线

| 路线 | 真实任务 | UF 来源 | 非线性整数来源 | 当前证据与实施边界 |
|---|---|---|---|---|
| Aztec / Apalache | 治理协议的不变量、反例和见证检查 | TLA+/Quint 的映射、集合等结构的 UF 编码；具体符号须核对导出日志 | 投票计算、符号配置下的整数除法和取模等 | 公开实际验证项目、规格和运行脚本；须提取并筛选查询 |
| TensorRight / XLA | 张量图重写是否保持语义 | 张量访问 `X(index...)` | 符号索引与步长的乘积、符号填充与膨胀属性 | 原始论文和开源实现；需接入 SMT 交互记录；reduction 的部分任务带量词 |
| VeriEQL | 有界 SQL 查询等价性、反例数据库 | 行到列值的函数、NULL 等 | 整数列乘积、聚合表达式 | 有源码证据和公开输入；String 标签及查询范围须处理，部分 UF 易消去 |
| 新合约 / Certora TAC | AMM、Vault 等性质检查 | 存储/bytemap 的上游编码 | 余额、储备量和比例计算中的乘除取模 | ctac 可接 TAC 导出；TAC 上游获取仍是依赖，读写 bytemap 可能引入数组 |

上述“优先”是实施建议，不是 SLS 性能实验结论。主要推荐 Aztec 和 TensorRight：前者有实际难例线索，后者提供不同于历史 Certora 家族的 UF/算术结构。

## 对“新”的要求

从新的真实输入、性质、源代码版本或有意义的应用配置导出任务，再对固定版本的官方库及提交库做去重。仅改变量名、断言顺序、注释、solver seed，不能作为新任务。未发现工具名、文件日期较新，都不能独立证明未收录。

最终报告只能说“在指定库快照和指定去重方法下未发现重复”；不要承诺已证明与全库任意公式语义不同。

## 本包没有做的事

- 没有执行 Aztec、TensorRight、VeriEQL 或 Certora 完整工具链。
- 没有取得私有 TAC、使用付费账户或申请外部权限。
- 没有完成 2026 最新 SMT-LIB 的逐文件比较。
- 没有打包第三方工程源码或论文 PDF；`SOURCES.md` 提供原始链接，后续按许可证取得并锁定版本。

上述边界应保留到生成报告中；后续完成某一步后，以实际证据更新。

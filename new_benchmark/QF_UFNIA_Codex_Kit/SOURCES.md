# 来源索引与核查边界

整理日期：2026-10-31。除特别说明，以下链接在本轮对话中通过原作者论文、官方项目文档/README或源码页面核查。没有因列出链接而声称下载、构建或执行成功。机器可读索引为 `sources.json`。

## 已有库、新颖性与理论

**S01 — SMT-LIB Explorer**  
<https://explore.smt-lib.org/>

核实 QF_UFNIA 已有 Certora、CLEARSY、Zohar-alive/ic、TwoSquares、UltimateLTLAutomizer 等家族。Explorer 可见目录不能穷尽所有最新提交；未出现新工具名不证明其公式从未收录。后续取得实际发布快照、记录 release 和 archive hash。

**S02 — SMT-LIB 2026 benchmark submission**  
<https://github.com/SMT-LIB/benchmark-submission>

官方 staging 仓库、目录、benchmark header 和提交要求。README说明自动生成的 benchmark 不应过于相似；记录生成器、应用、论文和日期。后续固定完整 commit，并同时检查正式发布库。

**S17 — 官方 logic 定义**  
<https://smt-lib.org/logics-all.shtml#UFNIA>  
<https://smt-lib.org/logics-all.shtml#QF_NIA>

已读 UFNIA/QF_NIA 的定义：整数签名加相应自由符号，相关 NIA 定义排除 `**`。这两个锚点用于核对片段边界，不声称此页面有一个单独列出的 QF_UFNIA 定义段。实际 QF_UFNIA 文件还需用支持该逻辑的合规解析器、类型环境和求解器核查。

**S18 — 官方 Ints 理论**  
<https://smt-lib.org/theories-Ints.shtml>

核实 div/mod 的欧几里得语义、负分母处理及零分母说明。当前理论页面 2026-01-16 更新加入指数，但“理论有该符号”不等于 NIA 片段允许该符号。本包默认不使用 `**`。

## Aztec / Apalache

**S03 — Aztec 公开验证工程**  
<https://github.com/konnov/aztec-governance-formal-verification-2025q3>

核查公开 Quint 规格、报告、源代码子模块和分解运行说明。README 的单合约安全性质命令及 inductive 脚本可作为入口；没有实际运行。工程为特定时期协议规格，不等同当前生产字节码。

**S04 — Thomas Pani、Igor Konnov，Formal Verification of the Aztec Governance Protocol，2025-12-09**  
<https://protocols-made-fun.com/aztec-governance.html>

原作者的实际项目说明；核实 125 个不变量、992 个任务，以及非线性投票和符号 ROUND_SIZE 取模难例。报告中的这些数字不是已筛出的 QF_UFNIA benchmark 数量。§7.4 给见证任务，§8 讨论 NIA 性能与未证实任务。

**S05 — Apalache fine tuning**  
<https://apalache-mc.org/docs/apalache/tuning.html>

核实默认 UF/数组编码的 logic 配置区别，以及 CVC5 使用 `cvc5.smt.logic=QF_UFNIA` 的非线性选项。参数传给 Quint/Apalache 的具体路径需按固定版本查明。

**S06 — ADR-009: Apalache Outputs**  
<https://apalache-mc.org/docs/adr/009adr-outputs.html>

输出设计列有 `log0.smt`，可作为日志定位线索；不是对所有版本、运行模式自动完整输出的保证。

**S07 — Apalache integer semantics**  
<https://apalache-mc.org/docs/lang/integers.html>

官方用 `2*x` 与 `x*y` 区分线性/非线性整数表达式。帮助筛选真正的非线性，而不是只看使用了 Int 或乘号。

## TensorRight / XLA

**S08 — Jai Arora 等，TensorRight: Automated Verification of Tensor Graph Rewrites，POPL 2025**  
DOI：<https://doi.org/10.1145/3704865>  
作者 PDF：<https://lsrcz.github.io/files/POPL25.pdf>

核实张量-as-UF、无界整数尺寸/索引、固定 rank 的验证条件；§6.4 为形状/访问/元素检查，§6.5 的 reduction 双射检查可能带量词。Fig.11 展示符号填充/膨胀乘积。论文版本不支持 reshape/bitcast 等布局敏感规则。不能把全部任务直接叫作 QF_UFNIA。

**S09 — TensorRight 官方代码**  
<https://github.com/ADAPT-uiuc/TensorRight>

核查 README、构建和部分模块/可执行配置入口。部分深层源码未完整读取；实施时应读锁定版本验证调用链。当前 README 与论文规则数量不同，不混用统计。

**S10 — TensorRight 论文 artifact 指针**  
<https://zenodo.org/records/14159871>

作为候选复现实验入口提供。此指针由调研定位，本包没有打开检查其全部档案内容，也没有下载/解压或运行。后续核对文件、版本、许可证及校验和，不预设具体内部目录。

**S11 — Grisette.Backend 官方 API**  
<https://lsrcz.github.io/grisette/Grisette-Backend.html>

核实 `GrisetteSMTConfig.sbvConfig` 与 `SMTConfig.transcript` 可用于记录交互。已读页面为 0.13 系列；与工程锁定依赖的接口可能不同，需核对后实施，不盲目升级，也不编造 TensorRight CLI 参数。

## VeriEQL / SQL

**S12 — Yang He、Pinhan Zhao、Xinyu Wang、Yuepeng Wang，VeriEQL: Bounded Equivalence Verification for Complex SQL Queries with Integrity Constraints，OOPSLA 2024**  
DOI：<https://doi.org/10.1145/3649849>  
预印本：<https://arxiv.org/abs/2403.03193>

论文与作者仓库的书目信息/应用方向已核实；预印本全文抓取未完成。不能据此声称已核查所有语义细节或全部 benchmark 理论归属。

**S13 — VeriEQL 官方仓库**  
<https://github.com/VeriEQL/VeriEQL>

README 列出 Calcite、Literature、LeetCode 输入和 Formula writer，提供运行与反例核对入口。整数乘法业务查询是本包的筛选/新输入建议，不是已逐一确认的原始查询特征。

**S14 — VeriEQL environment.py**  
<https://github.com/VeriEQL/VeriEQL/blob/main/environment.py>

部分源码已读，核实 `VarSort=IntSort`、列读取 UF 和 NULL 函数的 StringSort 标签。由此得到必须处理字符串理论的边界。具体标签约束及变换的正确性仍需读锁定源码并验证。

## 合约 / TAC

**S15 — 公开 Solidity 验证任务集**  
<https://github.com/fsainas/contracts-verification-benchmark>

相关论文：M. Bartoletti、F. Fioravanti、G. Matricardi、R. Pettinau、F. Sainas，*Towards Benchmarking of Solidity verification tools*，FMBC 2024（作者仓库所列标题）。核实 README 描述 323 个任务、正确/缺陷版本、AMM/Vault 等用例。未确认每个任务能导出 UF+NIA 或已取得 TAC。

**S16 — ctac**  
<https://github.com/1arie1/ctac>

核实 TAC/.sbf.json 输入、`ua` 与 `smt` 导出示例、loop-free/single-assert 前提及默认 sea_vc 的说明。README 同时提示读写 bytemap 使用 Store/Select，须逐输出核查理论。工具不独立从 Solidity 编译。未构建或运行，TAC 获取和输出可共享范围另行核实。

## 后续检索规则

上述仓库 URL 不是实验版本号。每次实施保存完整 SHA、输入 hash、运行命令、query_index、实际边界和输出 hash。若新增来源，补充同样的“已核实支持什么、尚未证明什么”，不能仅放链接后赋予它未提供的生成能力。

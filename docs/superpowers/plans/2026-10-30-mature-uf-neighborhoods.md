# 在完整GMP内核中直接处理UF邻域

目标不变：用户指定sat集合572例，只有node3、180并行、每例端到端600秒，至少400个不同例子的原公式验证SAT。v10b短筛已经finished：555例得到338SAT、215unknown、2timeout，实际峰值180，无新增OOM；独立audit-m00jmin0无问题。正式v10仍使用不可变快照，不受本计划开发影响。

## 实际动机

v10b未知Certora中至少127例经历UF精化，部分多达46轮。历史Yices仅状态交叉证明至少109个精化组未知例曾SAT，未读取或复用竞品模型。完整搜索内核已经解开部分旧算术/Boolean平台，但只在候选模型之后处理UF会反复转换、重新搜索。将已有core的UF桶、实参分离与结果组修复接入完整mature内核，以真实反馈指导搜索。

## 实施边界

- 不修改任何v10b快照、输入benchmark及原NIA_LS。当前正式600秒轮由实验代理在已授权门槛满足后启动；开发只做server上的合成单测，不在node3叠加benchmark。
- 默认mature模式保持原plain增量搜索。显式`--backend mature --uf-observers`启用新路径，支持`--interaction none|soft|joint`和`--scoring count|paws`；不适用的其他core选项仍拒绝，报告真实生效配置。
- 新观察模式明确报告`mature-mpz-v2`，原模式保留v1身份。输入CNF与UF观察表放同一原子native文件，可选尾段必须有版本、完整计数、带类型的变量名。默认reader不接受意外尾段。
- 前端复用受保护UF marker经过现有Z3预处理，提取其变换后的实参与结果。复合Int表达式以精确定义物化，复合Bool表达式以等价CNF reify；纯观察变量由表注册，不添加限制值的假约束。仍保留Goal模型还原、有限UF解释与原断言验证。
- 内核保留原term/literal/watch/CSR评分，加入按函数和具体实参的UF桶、变量到应用索引、受影响桶的试探差分。普通候选得分加入UF变化；Boolean候选在原缓存分之外计算UF变化。联合候选同时评分受影响clauses/terms/apps并集，再一致提交各分量并失效缓存。
- 新邻域包括冲突结果组联合赋值和实参分离；必要界及固定值仍合法。CNF全真而UF冲突非零时有专用搜索分支，不能调用空集合random_walk，也不能错误提前宣告原公式SAT。
- `none`基线的验证scope和外部精化语义由核心/前端共同明确，原模型验证永不省略。soft/joint的原生终止须满足其声明的UF一致性门槛。

## 验证与实验

先执行会失败的真实构造：CNF零但UF冲突、结果组需多变量联合修改、实参分离优于错误合并、Bool参数/结果、复合观察、solve-eqs消元后的result。保留原模式和差分评分验证，检查最终模型满足原SMT公式。禁止调用完整SMT搜索或竞品模型。

实现后冻结独立v11配置；等正在运行的正式v10结束并审计后，再由root决定node3唯一下一轮。不得将两个配置SAT并集当400验收。

分工：核心代理独占`UFNIA_LS/mature/`及成熟核心测试；前端代理独占converter/driver/native frontend及其测试；实验代理维护runner/audit并负责已明确授权的单次正式v10启动；root整合、独立复查和最终验收。

## v11结果与v12控制修复

v11已实现并通过root fresh158根测试、27driver及71scripts，共256项；28项成熟核心三宏差分测试通过，binary SHA `5dc42c6f4916e0250c96cf889460363da561ab1c273c84c6e193ffc1c9ac4409`。包含少数结果类公平候选、原子patch精确评分/回滚、Bool变量与unit-linear物化补偿。后续runner另修复最终后处理跨deadline时SAT误分类的问题，两项慢query/cleanup实测RED→GREEN，完整scripts变为73项；不可变v11快照未被修改。

真实node3 v11 joint+paws/30秒短筛发生回退。root在552/555时停止：271SAT、281unknown、余3最高274，无法追平同预算v10短筛338；目录`uf_mature_uf_v11_joint_paws_screen_node3/run-20261030T172129Z-obu2c14p`，原PID3563040，停止依据`root_stop_reason.json`。该轮不完整，必须独立审计且不具备400验收资格，不能累计不同版本的SAT。旧Certora从14增加到27，但ic180→116、alive103→95，不能以局部增益代替总体目标。

72个ic/alive回退样本均曾v10 eager，UF应用6–8、pairbound15–28；v11全部算术历史最优0、末UF冲突1–2，均为core未找到模型，不是已经观察到的模型还原失败。atoms/clauses/前端耗时大多减少，不能归因为普遍消元膨胀。代表样本在30秒内走145k–2.15m步，UFescape超过百万，普通Int阶段仅几十至几百步。

源码定位了独立的控制缺口：CNF0/UF>0专用分支在`continue`之前不更新PAWS、不检查停滞重启，UF候选也不读取`critical_move`记录的方向tabu。root授权v12先修复这一实证问题：共同deadline与restart检查提前，UF-only无正增益时按真实count/paws策略重加权，使用Int/Bool各自时钟的tabu与明确的SAT/newbest aspiration。保持v1路径及SAT精确门槛，不修改任何冻结版本，不先用small-eager掩盖控制问题。新统计须将当前CNF/UF和best-total时成对CNF/UF分开，避免历史CNF最小值与末态UF值被拼接成不存在的联合SAT。

此前v10正式轮同样已停止：当自报386SAT、余10时数学上最多396<400。独立审计实际保留385；另1例虽模型验证通过但CSV耗时618.873秒，严格排除。审计`formal600_mature_v10_node3/run-20261030T165652Z-mbqu2c6c/audit-2f0joapm`，interrupted/eligible=false。400目标仍未完成。

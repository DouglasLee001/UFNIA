# 保留最佳实现并清理实验副本

> **For agentic workers:** use subagent-driven-development for the independent promotion and cleanup tasks.

**Goal:** 把已验证70/191的POINT-OFF策略整理为主目录实现，移除较差实验的实现与编译副本，保留结果及研究结论。

**Architecture:** 保留原始 snapshots/affine_point_units_v1 及其正式审计不变。仅同步其中成熟内核到 UFNIA_LS/mature；删除POINT专属接线和文件，保留单根affine、精确cache及v12联合评分/提交。常规mature默认开启affine/cache，CLI后端选择保持兼容。历史成绩仍绑定原冻结二进制，清理后构建通过回归验证，不冒称已经重新跑出70。

**Tech Stack:** C++17/GMP，现有Python/Z3前端，unittest，node2/node3。

## 任务与检查

- [x] 固定最佳snapshot f33004…、core28cf0f…和既有用户改动清单。当前主目录binary641a…不是最佳。
- [x] 推广成熟源：保留除POINT之外的最佳源码；main仅保留POINT=0分支、删POINT后缀/member和Makefile依赖。缓存默认true、可用原开关关闭。复制最佳私有GMP到忽略的build/ufnia_ls/deps/gmp，并让Makefile在其存在时静态链接，否则沿用系统GMP。只调整现有control测试编译依赖以链接nia_affine及私有GMP，不改变断言；为默认最佳配置做实际人工输入RED/GREEN。
- [x] node2单编译器正常O2构建；运行原7模块66回归及新的默认配置测试。只用新人工输入，不运行真实benchmark短筛，不调用完整SMT求解器。
- [x] 对result/certora2024/research与除最佳之外的snapshots生成逐文件清理manifest：移除实验源码、脚本副本、库/目标/ELF及pycache；保留原式/模型/赋值/native/capture/privateprogress内容、JSON/CSV/日志/报告等结果证据，不读取受限payload。保存manifest，核实无当前node2/node3求解进程在使用它们，再执行。保护整个最佳snapshot、根源码/competitors/NIA_LS/new_benchmark和非本轮结果集合。
- [x] 清理根research下已确认空的人工实验目录。同步README、实验说明、当前结构设计和待尝试方向：现在主实现已同步最佳；已删除原型不再写成现存可执行实现；保留负结果及下一方向研究记录。
- [x] 独立SPEC与QUALITY审阅，核对最佳snapshot全部登记资产SHA不变、结果文件未误删、清理manifest所有对象消失、主入口实际人工SMT原模型验证通过。给用户留下运行命令和精简总结。

本次用户明确授权保留最佳、移除其它较差尝试；因此直接执行具体清理，不另建功能或恢复130目标搜索。不提交.git，不覆盖原有README/汇总器等无关用户修改；仅在其现有内容前追加当前状态。

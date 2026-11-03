# 模板填写规则

这些文件是实施模板，不是执行记录或合格实例。

- `campaign.template.yaml`：建议 pilot 和扩展配置。修改实际资源参数后保存为输出工程的 campaign。
- `benchmark_header.smt2.in`：文件头示意；包含占位符，故不是可运行 benchmark。提交前按实际 SMT-LIB release 校核格式和许可证。
- `instance_metadata.template.json`：可解析、可通过候选结构检查的未填写记录；`TEMPLATE_NOT_AN_INSTANCE`、null、not_run 都表示尚未实施。
- `manifest.schema.json`：对每个 JSON 或 JSONL 记录做结构校验。`dataset_role=main` 有更严格要求。结构校验不能证明字段真实、编码正确或去重充分，验收还须检查文件、hash 和日志。

`encoding.transformation_relation` 指**捕获的完整 raw 查询到交付 SMT2**的关系，不是“自建模型等价于生产程序”。应用派生模型的保真范围写在 `application.scope_description`、`encoding.semantic_limitations` 和单独模型说明中。

对 native/derived incremental 文件，在 `query.per_query_records` 为每个检查保存 query_index、status、原始 check 命令、当次 assumptions、活跃断言 hash、假设 hash 和证据位置。文件顶层 status 为所有 query 状态一致时的状态，混合时为 `mixed`；SMT 文件中仍为每次 query 分别写 status。`solver_runs[].per_query_results` 保存顺序结果，不以顶层 mixed 替代逐查询结果。

元数据中 `status=unknown` 代表基准真值尚未确认；`solver_runs[].result` 可以区分 timeout/error/unsupported。成功的主集未知实例仍需有实际尝试记录，不能只插一条 `not_run` 通过结构检查。

最终逐项检查：没有占位符、所有相对路径真实存在、最终 bytes 的 SHA256 一致、主集 source_commit 已填完整 SHA、真实 solver 记录可读、去重范围有版本和方法。未完成的候选继续留在 candidate，不能标为 main。

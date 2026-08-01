# 用户端按文件删除本次构建全部产物方案

## 1. 状态、口径与实施边界

本方案已按产品确认口径复核：删除操作针对一个稳定的 `IngestionItem.item_id`，目标是
让该文件本次构建产生的全部专属产物退出系统，不只删除正式入库记录。当前只完成代码、
Alembic、SQLAlchemy 元数据和运行中 PostgreSQL 结构审计；不实施迁移、接口、Worker、
前端按钮或任何数据删除。

“全部产物”按数据性质分为三类：

1. 文件专属且可重建的投影、匹配、识别和物化结果：物理删除；
2. 已进入审批、治理或版本生命周期的专属对象：退休，退出活动入口但保留审计内容；
3. 原始物理证据、共享发布资产和历史审计：保留，不能因删除单文件结果而破坏。

因此，“全部产物已删除”表示该文件不再存在任何可继续使用、查询、审批、物化或参与
问数的构建产物，不等于把证明这些操作曾经发生过的不可变证据和审计记录抹掉。

必须保留：

- `IngestionItem`、所属批次、租户和行政区划范围；
- 原始上传文件、`source_path`、`source_sha256`、文件大小；
- `document_profiles` 中不可变的原始单元格和物理布局证据；
- 已发布或用户确认的字段、模板及四层模板版本；
- 治理决议、替换声明、任务审计和删除审计；
- 历史问数会话、运行记录及确定性事实快照。

删除成功后，旧文件从全部用户文件台账、批次 items、详情和筛选计数中消失，不提供
“已删除”用户入口。后端仅保留架构要求的不可变物理证据和删除审计。用户上传同名但
内容或行数变化的新文件时，新的 SHA-256 创建新的 `IngestionItem` 并重新构建；文件名
不作为删除或替换身份。内容完全相同的文件继续受现有村级 SHA-256 去重约束。

## 2. 现状与不能复用重新入库的原因

上传去重键是：

```text
tenant_id + administrative_unit_id + source_sha256
```

同名文件内容变化后会创建新文件项，但旧文件的正式记录和其他构建投影仍然存在。如不
显式删除/退休，旧、新两份产物都可能出现在记录、治理和问数入口。

当前 `reset_item_for_reimport()` 不能直接复用：

- 它要求磁盘内容 SHA-256 与首次接收时一致，并在清理后立即重新排队处理原文件；
- 它删除 `document_profiles`，违反本方案保留不可变原始物理证据的边界；
- 它物理删除 `approved_import_plans`、`template_proposals` 和全部旧 `jobs`，会破坏审批、
  治理和任务审计；
- 它只直接删除 `template_matches`、`region_template_matches`，没有在同一清理事务中删除
  `document_sheet_catalog`、`field_matches`、`sheet_composition_matches`、
  `workbook_route_matches`；后续重跑若中途失败，会留下旧投影窗口；
- 它没有“结果已删除”的终态、幂等删除审计、并发互斥和未来外键漂移保护。

所以方向上它包含部分清理动作，但既删了不该删的审计/证据，又漏了应删的文件专属
投影，且业务终态不同。实现必须新增独立删除命令，不能调用或扩展该函数。

## 3. 复核范围与结论

本次清单由四条路径交叉核对：

1. 从解析、匹配、Hermes、治理、计划批准、物化和问数代码追踪每个写入点；
2. 从 SQLAlchemy `Base.metadata` 生成所有直接或间接关联 `ingestion_items`、
   `approved_import_plans`、`dataset_records`、`record_index_values` 的外键图；
3. 核对 Alembic 约束及 `ON DELETE` 规则；
4. 核对当前 PostgreSQL `alembic_version = 20260731_0046` 的真实外键和关联列。

工作区当前新增的 0046 仅给 `semantic_field_versions` 增加来源元数据，没有新增单文件
构建结果外键。ORM 元数据中 24 张直接或间接关联表均已在本方案中归入“物理删除、
退休/更新、保留”之一，实际 PostgreSQL 未发现 ORM 清单之外的结果子表。

当前 146 个文件的只读一致性核查中，以下异常均为 0：

- 正式记录所属 item 与计划所属 item 不一致；
- 正式记录租户、行政区划、批次与 item 不一致；
- 质量问题 item 与计划 item 不一致；
- 导入执行缺少计划；
- 记录索引缺少正式记录；
- 字段血缘缺少记录索引。

这只证明当前数据满足 manifest 的正向归属条件；实现必须在每次删除事务内重新验证。

当前写入链按阶段为：

```text
原始证据
  document_profiles                         保留
    -> document_sheet_catalog              物理删除

匹配/识别
  template_matches                         物理删除
  region_template_matches                  物理删除
  field_matches                            物理删除
  sheet_composition_matches                物理删除
  workbook_route_matches                   物理删除
  hermes_recognition_records               物理删除
  hermes_recognition_cache                 共享缓存，保留

治理/批准
  template_proposals                       退休并保留内容
  provisional template_versions            退休并保留版本证据
  approved_import_plans                    退休并保留不可变计划
  governance resolutions / shared assets   保留

物化
  import_executions                        物理删除
  quality_issues                           物理删除
  dataset_records
    -> record_index_values
      -> record_value_lineage               物理删除
```

## 4. 表级策略

### 4.1 必须物理删除的十二张表

| 顺序 | 表 | 精确范围 | 原因 |
|---|---|---|---|
| 1 | `record_value_lineage` | 索引属于目标 item 的正式记录 | 已删除字段索引的单元格血缘 |
| 2 | `record_index_values` | 正式记录属于目标 item | 确定性查询索引 |
| 3 | `dataset_records` | `item_id`、计划和范围全部匹配 manifest | 正式 JSONB 业务记录 |
| 4 | `quality_issues` | `item_id = target_item_id`，包括无计划问题 | 本次构建产生的解析、匹配、治理和物化问题投影 |
| 5 | `import_executions` | 计划属于目标 item | 本次物化执行结果 |
| 6 | `hermes_recognition_records` | `item_id = target_item_id` | 文件专属 Hermes 调用关联；共享 cache 不删 |
| 7 | `field_matches` | `item_id = target_item_id` | 字段候选/匹配投影 |
| 8 | `region_template_matches` | `item_id = target_item_id` | Region 匹配投影 |
| 9 | `sheet_composition_matches` | `item_id = target_item_id` | Sheet 组合匹配投影 |
| 10 | `workbook_route_matches` | `item_id = target_item_id` | Workbook 路由匹配投影 |
| 11 | `template_matches` | `item_id = target_item_id` | 文件级模板匹配投影 |
| 12 | `document_sheet_catalog` | `item_id = target_item_id` | 由不可变 profile 重建的 Sheet 查询目录 |

这里删除全部目标 item 的 `quality_issues`，不再只删除计划绑定问题。原因是用户确认的
口径已从“只删正式结果”扩大为“删本次构建全部产物”；质量问题是派生判断，不是原始
物理证据。治理原始理由仍由保留的提案、决议和删除审计证明。

所有十二张表都应显式删除并对账，即使数据库存在 `CASCADE`，也不能靠级联隐藏实际
影响范围。

### 4.2 必须退休但不能物理删除的对象

| 对象 | 退休规则 |
|---|---|
| `template_proposals` | 保留 proposal、处理状态和处理人；增加独立 retirement 元数据，pending 提案立即退出待审队列，accepted/rejected 的原结论不被覆盖 |
| `approved_import_plans` | 计划正文和批准信息保持不可变；增加 retirement 元数据，禁止再次物化或作为新运行输入 |
| `template_versions` 中本 item 独占的 Hermes 临时版本 | 仅 `source = hermes_provisional`、`status = admin_review` 且 `source_metadata.source_item_id` 精确匹配时退休；保留版本内容并退出目录、审批和匹配候选 |

建议给这三类对象增加统一含义的 `build_result_retired_at`、
`build_result_retired_by_deletion_id`。退休字段是生命周期元数据，不改写提案原 resolution、
导入计划正文或模板版本定义。

以下对象即使来源元数据提到目标 item，也不能退休或删除：

- `published`、`user_confirmed` 或已经被其他文件引用的模板版本；
- 已发布语义字段及其版本、variants；
- Region、Sheet 组合、Workbook 路由及其版本；
- `template_region_components` 等共享模板组成部分。

若某临时模板版本无法证明“admin_review + hermes_provisional + 精确 item 归属”，预检必须
报 `BUILD_RESULT_DELETE_SHARED_ASSET_AMBIGUOUS` 并停止。历史重新入库可能已删除旧 proposal，
这种情况下允许在确认 proposal 已不存在且没有其他 item 的活动计划引用后退休该孤立版本，
并在删除审计中记录；若 proposal 仍存在且属于其他 item，则必须阻断。

### 4.3 必须更新的运行状态与审计

| 表 | 计划变更 |
|---|---|
| `ingestion_items` | 增加独立删除状态、删除时间、操作人；成功后通用状态为 `result_deleted`、正式入库状态为 `deleted` |
| `jobs` | 新增 `cancelled` 状态和 `DELETE_BUILD_RESULT` 任务；取消 pending，拒绝并发 running，所有历史任务保留 |
| `ingestion_batches` | 重算完成、失败、删除数量和批次状态，不减少 `total_files` |
| `ingestion_build_result_deletions` | 新增不可变审计，唯一绑定 item，记录 manifest、十二表删除数量、三类退休对象 ID、请求人和完成时间 |

建议状态：

```text
ingestion_items.build_result_deletion_status
  active | deletion_pending | deleting | deleted | deletion_failed
ingestion_items.build_result_deleted_at
ingestion_items.build_result_deleted_by_user_id
ingestion_batches.deleted_files
```

删除失败时，物理删除、退休标记、审计和 item 状态在同一事务中回滚，原产物仍完整可用；
只有事务成功后才进入 `deleted`。

### 4.4 必须保留的表和对象

| 表或对象 | 保留理由 |
|---|---|
| `ingestion_items`、`ingestion_batches` | 文件身份、范围、SHA-256、批次总账 |
| 原始上传文件 | 不可变物理证据，不 `unlink`、移动或覆盖 |
| `document_profiles` | 原始单元格及物理布局证据；这是与可重建 Sheet 目录的关键边界 |
| `hermes_recognition_cache` | 按内容/契约共享，可能被其他文件引用 |
| `governance_resolutions`、`governance_field_resolutions` | 已提交治理决议和字段映射证据 |
| `semantic_ignore_rules` | 已生效规则，可能被其他文件复用 |
| `ingestion_item_supersessions` | 不可变来源替换声明 |
| 已发布/用户确认的字段及四层模板资产 | 共享、版本化资产，不属于单文件独占产物 |
| `jobs` | 成功、失败、取消和删除任务审计 |
| `question_conversations`、`question_runs` | 历史会话和运行记录 |
| `question_fact_results` | 当时接受的确定性结果和数据集快照 |

保留对象必须通过删除状态或 retirement 元数据与活动入口隔离；“保留审计”不能导致它
继续参与审批、匹配、物化或问数。

## 5. API、并发与事务

### 5.1 API

```http
DELETE /api/batches/{batch_id}/items/{item_id}/build-result
```

接口只接收稳定 ID，不接收租户、村、计划 ID 或待删除表名。权限复用并强化现有批次
访问和入库权限，验证 item 与 batch 的租户、行政区划、创建者范围完全一致。

首次请求在行锁下：

1. 锁定 item 和该 item 的全部 Job；
2. 若存在除删除任务外的 running Job，返回 409；
3. 将所有 pending Job 改为 `cancelled`，原因固定为
   `BUILD_RESULT_DELETE_REQUESTED`，不删除任务、不消耗重试次数；
4. 原子写入 `deletion_pending` 并排入唯一删除任务；
5. 返回 202 和删除操作状态。

重复请求返回同一个删除审计/任务，不创建第二次删除。

### 5.2 Worker 隔离

`DELETE_BUILD_RESULT` 与物化任务共用互斥 lane。所有普通 Job 在作用域校验中增加删除
状态门：item 为 `deletion_pending/deleting/deleted` 时不得继续写入。

当前 `release_for_shutdown()`、`defer_for_operator_action()` 都可能把任务恢复为 pending，
所以删除请求必须在锁内取消 pending；已经 running 的任务不能被强行越过。删除 Worker
在同一 PostgreSQL 事务中完成预检、物理删除、退休、后验校验、审计和状态更新；任一
失败全部回滚。

### 5.3 确定性 manifest 与预检

删除前生成只存在于事务内的 manifest：

```text
tenant_id
administrative_unit_id
batch_id
item_id
source_sha256
approved_plan_ids
proposal_ids
exclusive_provisional_template_version_ids
dataset_record_ids
record_index_value_ids
十二张物理删除表的删除前数量
三类退休对象的删除前状态
```

正向证明要求：

- 所有计划、提案和十二表目标行都可从目标 item 正向归属；
- 正式记录的 item、计划、租户、行政区划、批次全部匹配；
- 不存在目标 item 的记录引用其他 item 的计划，也不存在目标计划拥有其他 item 记录；
- 临时模板版本必须满足 4.2 的独占条件，且不得已发布、用户确认或被其他 item 活动引用；
- `HermesRecognitionCache`、共享模板组件和发布资产不进入删除集合。

任何范围异常都返回 `BUILD_RESULT_DELETE_SCOPE_MISMATCH` 并零删除。

运行时还要读取 PostgreSQL 外键元数据，把全部 item/plan/record 子表集合与策略 allowlist
比较。新增关联表但方案未分类时返回 `BUILD_RESULT_DELETE_POLICY_DRIFT`，禁止未知级联。

### 5.4 执行顺序与后验断言

同一事务中严格执行：

1. `record_value_lineage`；
2. `record_index_values`；
3. `dataset_records`；
4. 全部目标 `quality_issues`；
5. 目标计划的 `import_executions`；
6. `hermes_recognition_records`；
7. `field_matches`；
8. `region_template_matches`；
9. `sheet_composition_matches`；
10. `workbook_route_matches`；
11. `template_matches`；
12. `document_sheet_catalog`；
13. 给提案、计划和独占临时模板版本写 retirement 元数据；
14. 断言十二张表目标计数均为 0，三类对象均已退休；
15. 核对实际删除/退休数与 manifest 完全相等；
16. 写不可变删除审计，更新 item/batch 状态并提交。

零记录或中途失败的文件也允许删除全部已产生的中间投影。重复执行读取唯一删除记录并
返回原结果，不扩大范围。

## 6. 活动数据、替换链与历史问数隔离

所有活动入口都必须显式要求 item 删除状态为 active，包括：

- 文件问数来源和 `freeze_question_scope()`；
- 记录列表、聚合和有效记录统计；
- 治理待办、提案审批和模板候选；
- 新建/批准计划、物化、重新入库及全部 Worker 写入；
- 对临时模板版本的目录、匹配和审批查询。

删除后的统一错误为：“该文件的构建产物已删除，请上传新版文件”。

当前 `source_supersession_map()` 要求 replacement 仍有正式记录才让替换声明生效，这会
在 replacement 删除后错误复活 superseded 文件。实现必须改为：不可变替换声明一旦
存在，旧源就不自动回到默认问数；允许暂时没有当前版本，等待新版上传。

历史问数拆分为：

- 历史展示模式：校验用户和数据范围后，允许读取原会话、运行、证据和 fact snapshot；
- 新运行模式：额外要求来源 active 且仍有有效正式记录，否则返回 409。

历史答案不重算、不改写，也不把旧快照带入新问题。

## 7. 前端交互

用户端 `BatchPage` 在稳定状态显示“删除本次构建产物”。处理中的文件、平台只读身份和
已经删除的文件不显示可执行按钮。

确认框显示：文件名、当前正式记录数、将删除/退休的产物分类，并明确：

- 记录、匹配、识别、中间目录和质量问题将退出系统；
- 原始文件、物理证据、共享发布资产和历史审计保留；
- 删除后旧文件不再参与查询、问数、审批或物化；
- 内容变化的新文件需要重新上传。

状态：

```text
删除本次构建产物 -> 等待删除 -> 正在删除 -> 构建产物已删除
                                      -> 删除失败，可重试
```

成功后关闭详情并刷新文件、批次、治理和记录统计。该文件从全部用户台账、顶部总数、
批次 items、记录页、问数来源和治理待办中消失；不新增“构建产物已删除”筛选标签。

## 8. 同名新版文件规则

- 同名、内容变化：SHA-256 不同，上传后创建新 item；
- 同名、内容相同：继续命中村级 SHA-256 去重；
- 新文件构建失败：旧结果不自动复活；
- 不按文件名模糊推断替换关系；
- 后续一键替换必须显式携带旧 `item_id`，复用确定性删除命令。

## 9. 验证与验收

### 9.1 聚焦测试

1. 成功删除一个完成构建的文件，十二张物理删除表目标范围全部为 0；
2. pending/accepted/rejected 提案保留原结论但均退出活动入口；目标计划不能再物化；
3. 仅独占的 `admin_review + hermes_provisional` 临时版本退休，published、
   user_confirmed 和其他文件共享版本不变；
4. 原始文件、`document_profiles`、治理决议、替换声明、Job 和历史问数快照保持不变；
5. 其他 item、村、租户及共享 cache/模板/字段计数和内容不变；
6. pending Job 转 cancelled；running Job、范围不一致、共享资产归属不明、策略漂移均
   阻断且零删除；
7. 中途异常事务完整回滚，重复 DELETE 幂等返回首次结果；
8. 零正式记录但有 profile/match/proposal/quality issue 的失败文件也能清理完整；
9. 删除后记录、治理、新问数、旧会话继续运行和重新物化均被阻止；
10. 历史会话和事实快照仍可查看；
11. 同名不同内容新版可上传，最终只有新版参与活动查询；同内容仍受去重保护；
12. 权限范围、租户、行政区划和批次边界不可越过；
13. 删除 replacement 后，superseded 文件不自动复活。

### 9.2 数据库级验收

在真实 PostgreSQL 事务中构造两个租户、两个村、多个文件以及共享模板/字段交叉引用，
对删除前后做全库相关表快照对账。允许变化仅限：

- 十二张表中目标 manifest 的行；
- 三类对象的 retirement 元数据；
- item/batch 删除状态；
- pending Job 的 cancelled 状态、删除 Job 和删除审计。

其他范围的行数和内容必须一致。ORM metadata 与 PostgreSQL 外键分别生成关联子图，与
策略 allowlist 精确比较；新增关联表未分类时测试必须失败。

### 9.3 工程检查

```bash
uv run pytest <删除功能聚焦测试>
uv run pytest
uv run ruff check .
uv run mypy src
cd frontend && npm run type-check
cd frontend && npm run test
cd frontend && npm run build
```

验收分别报告聚焦测试、完整流水线和真实 PostgreSQL 对账，不能只以 API 或 SQL 成功
作为通过。

## 10. 计划实施文件

方案再次确认后，预计修改：

- Alembic：item/batch 删除状态、三类 retirement 元数据和不可变删除审计；
- `db/models.py`、`db/schema.py`：状态、审计和 API 响应；
- 新增 `build_result_deletion.py`：策略、manifest、预检、事务和后验断言；
- `jobs/queue.py`、`worker.py`：删除任务、互斥和已删除 item 写入门；
- `api/routes/batches.py`：权限受控、幂等 DELETE；
- `api/routes/files.py`、`records.py`、`reviews.py`、`questions.py`、
  `question_scope.py` 及模板目录/匹配查询：活动、退休与历史隔离；
- `frontend/src/lib/api.ts`、`pages/BatchPage.tsx`、`styles.css`：确认框、状态、轮询和
  上传新版引导；
- 后端、前端和数据库聚焦回归测试。

本方案不授权当前实施，也不授权清理任何服务器现有数据。

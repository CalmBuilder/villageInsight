# 模板覆盖的合成村情数据集与问数金标方案

## 目标

生成一套可以随 Git 交付的合成数据，用于用户演练文件导入、四层模板匹配、正式物化和
问数。所有人员、证件、电话、地址、行政区和组织名称均为显式测试值，不复制
`docs/datafiles` 的数据单元格；只使用由真实语料验收形成的已发布模板结构。

## 首版范围

首版使用两个已经发布且由 Region、Sheet、Workbook Route 完整覆盖的单表模板：

1. 户籍人口模板：
   - Route `workbook.structured.d16e0f18c03f9fdc9795@2`
   - Sheet `sheet.structured.68231c630e9990629fef@1`
   - Region `region.population.beea70777607695d924f@1`
   - 生成 180 人、60 户。
2. 党员名册模板：
   - Route `workbook.structured.c47e78ae07d0f8eb8293@2`
   - Sheet `sheet.structured.e18df584b6d7cb597994@1`
   - Region `region.population.8fef505486bbe5584252@1`
   - 生成 120 人，并与户籍人口中的前 120 个合成人员确定性关联。

生成器必须先连接当前 PostgreSQL，验证上述发布版本、槽位关系、表头顺序和字段绑定
完全一致；模板漂移时拒绝生成，不能产出“看起来相似但实际不命中”的文件。

## 数据规则

- 行政区固定为 `演示县 / 演示镇 / 演示一村`。
- 姓名使用 `演示居民0001`；户号使用 `DEMO-HH-0001`。
- 身份证使用 `TEST-ID-000001`，电话使用 `TEST-PHONE-000001`，均不伪装成可联系或
  可认证的真实标识符。
- 不复制原工作簿样式、批注、图片、隐藏 Sheet、公式、宏、外部链接和文档属性。
- 使用固定算法而非随机 Faker，重复生成的业务内容和标准答案一致。
- 文件内不允许公式；所有数字答案由生成器直接从内存中的合成记录计算。

## 问题金标

至少生成 150 题，覆盖总数、分类计数、人员属性、家庭关系、年龄、学历、支部以及
身份证/手机号权限阻断。每题同时输出：

- 中文问题和显示答案；
- 机器可比对的值、类型和比较方式；
- 参考文件、语义字段及题目类别；
- 敏感问题的预期策略和 reason code，而不是泄露标识符。

输出 `questions.xlsx` 兼容现有 `所属村委 / 提问 / 参考表格 / 预期结果` 契约，另有
`questions.json` 作为确定性验收源。

## 验收

1. 在当前目录执行只回滚的模板覆盖检查：两个文件均无需 Hermes，Region、字段、Sheet
   和 Workbook Route 全部 exact。
2. 在独立 PostgreSQL 空库迁移到 head，恢复 Git 中的完整模板包，导入两个文件并完成
   materialization。
3. 对账 300 条源记录、模板/字段命中、正式记录数及合成数据金标。
4. 隔离库中选取计数、筛选、人员属性和敏感策略问题做确定性问数回归。
5. 检查生成目录未被 `.gitignore` 排除、无单文件超过 GitHub 100MB。

## 2026-08-01 实施验收

- 已生成 300 条记录：户籍人口 180 条、党员名册 120 条。
- 已生成 231 道金标：汇总 3 道、筛选计数 28 道、人员属性 180 道、敏感策略
  20 道。
- 当前模板覆盖报告 `accepted=true`；两个文件均不需要 Hermes，指定 Region、字段、
  Sheet、Workbook Route 的 ID 和版本全部 exact。
- 隔离库 `village_insight_synthetic_dataset_check_codex01` 从空库迁移到 head，恢复
  `current-205-expanded` 后通过真实 profile、match、自动模板计划和 materialization，
  正式记录数为 180 + 120。
- 231 道金标已逐题对照隔离库 `semantic_data`；敏感策略题同时通过现有题库分类器。
  验收过程中补充了正式字段名 `公民身份号码` 的敏感策略识别。

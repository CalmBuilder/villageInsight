# 标准字段变体来源值修复

## 问题

`semantic_field_variants` 中存在历史来源值
`codex_real_regression`。该值不属于 `SemanticFieldVariantInput.source`
允许的来源集合，导致 `GET /api/fields` 在响应序列化时返回 500。

受影响记录当前属于 `person.ancestral_hometown` 的已发布版本，其语义映射为
“籍贯省市县区”到“籍贯”。映射关系本身有效，异常仅位于来源元数据。

## 修复范围

- 将 `semantic_field_variants.source = 'codex_real_regression'` 规范化为
  `codex`。
- 保留字段、字段版本、变体键、表头路径、置信度和 `evidence`。
- 不扩展 API 写入契约，不允许后续继续写入非标准来源值。
- 通过 Alembic 数据迁移执行，使修复可审计并可在其他环境重复执行。

该修复不提供反向迁移。降级时重新写回无效来源会再次破坏现有 API 契约，
因此 `downgrade()` 保持数据不变。

## 验收

1. Alembic 可以升级到新增迁移。
2. 数据库不再存在 `source = 'codex_real_regression'` 的字段变体。
3. 原记录仍映射到 `person.ancestral_hometown`，且 `evidence` 保持不变。
4. 登录后请求 `GET /api/fields` 返回 200。
5. `GET /api/reviews` 和健康检查继续正常。

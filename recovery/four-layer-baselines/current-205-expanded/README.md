# current-205-expanded 四层模板完整恢复包

本目录由 Git 跟踪，包含四层模板完整关系数据，不包含用户、上传文件、批次、业务
JSONB 记录或问答数据。

- 文件：`catalog-bundle.json.gz`
- 大小：2,024,064 bytes
- 文件 SHA-256：`b8c7ee1c46d496d7ad17fa4959edb083c3ec13cf2816a84acf87f9b33fa848f7`
- 包逻辑摘要：`f4f6673a49d98eead2dc188de558e66eeac8219de8aa288385fbee7e5f9fd139`
- 字段 / Region / Sheet / 文件路由：`1075 / 386 / 328 / 210`

恢复包覆盖四层目录对象、所有版本、字段变体、Region/Sheet 槽位、Sheet/文件路由槽位
和审核事件。审核事件中的操作者文字证据会保留；目标库不存在对应用户时，可空的
`actor_user_id` 会置空，避免引入用户数据依赖。

```bash
./scripts/restore-four-layer-baseline.sh --list
./scripts/restore-four-layer-baseline.sh --dry-run
./scripts/restore-four-layer-baseline.sh --baseline current-205-expanded
```

正式恢复前会把目标库的完整模板目录导出到 `backups/four-layer-pre-restore/`。恢复过程
只写四层模板的 15 张关系表：补回缺失行、修复基线行、清理基线版本上额外的行为子项，
保留基线之后的模板版本和审核事件，并将发布状态切回本基线。业务表不在写入范围内。

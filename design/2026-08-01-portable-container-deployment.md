# 通用容器部署配置方案

## 目标

- PostgreSQL 镜像通过 `POSTGRES_IMAGE` 切换，并允许生产环境固定镜像摘要。
- 开发环境默认继续使用 Docker 命名卷，生产环境可通过绝对路径切换为 bind mount。
- PostgreSQL 映射端口默认只监听宿主机 `127.0.0.1`。
- API 和三个 Worker 从同一容器数据库连接变量读取凭据，不再在 Compose 中四处硬编码。
- 不增加未被现有工具使用的 `/backups` 和 init-scripts 挂载。
- 明确已有命名卷切换到宿主机目录时的备份、恢复和空库风险。

## 配置边界

- 根目录 `.env` 的 `DATABASE_URL` 供 `app.sh`、Alembic 和宿主机命令使用。
- `docker/.env` 的 `POSTGRES_APPLICATION_URL` 供全容器模式的 API 和 Worker 使用，
  主机名固定为 Compose 服务名 `postgres`、容器端口固定为 `5432`。
- `POSTGRES_DATA_DIR` 留空时使用 `village_insight_postgres_data` 命名卷；设置绝对路径
  时将该路径挂载到 `/var/lib/postgresql/data`。
- `POSTGRES_BIND_ADDRESS` 默认 `127.0.0.1`。需要远程数据库客户端时，应优先使用
  SSH 隧道；确需对外监听时必须同时配置防火墙和 PostgreSQL 访问控制。

## 迁移门禁

从已有命名卷切换到空宿主机目录不会自动搬迁数据，PostgreSQL 会把它初始化成新集群。
切换前必须完成并校验 `pg_dump -Fc`，停止写入和旧容器，准备目录权限，再启动新集群并
执行 `pg_restore`。恢复后还要执行 Alembic、业务健康检查和记录数对账。没有可验证备份
时不得切换。

数据库备份不得与 PGDATA 放在同一故障域。项目侧
`backups/four-layer-pre-restore/` 是模板恢复前快照，不等同于 PostgreSQL 整库备份。

## 本次不做

- 不挂载 `/docker-entrypoint-initdb.d`；结构升级继续由 Alembic 管理。
- 不挂载容器 `/backups`；待 pg_dump、校验、保留周期和恢复演练形成完整工具后再接入。
- 不自动迁移已有命名卷，也不删除任何现有卷或生产目录。

## 2026-08-01 实施验收

- 默认配置渲染为 `postgres_data` 命名卷；设置绝对 `POSTGRES_DATA_DIR` 后渲染为目标
  bind mount。
- PostgreSQL 发布端口的 `host_ip` 为 `127.0.0.1`。
- 自定义 `POSTGRES_APPLICATION_URL` 会一致注入 API 和三个 Worker。
- Compose 默认/生产目录配置、Shell 语法、应用配置测试、前端类型检查和工作区格式检查
  均通过。

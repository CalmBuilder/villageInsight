# 显式初始化账号设计

## 目标

空 PostgreSQL 数据库完成 Alembic 迁移后，只初始化两个可登录应用账号：

- `admin`：平台管理员，进入管理端；
- `demo`：村级数据员，绑定示例业务租户下的示例村，操作演示数据。

初始化不创建六村账号、租户管理员 `x` 或其他测试身份，也不写入真实业务数据、模型
密钥或生产凭据。

## 执行边界

数据库表结构继续由 Alembic 管理。账号和组织属于部署时引导数据，由应用级命令
`village-insight-bootstrap` 根据 `.env` 创建，不使用 `docker-entrypoint-initdb.d`
SQL。固定执行顺序为：

```text
alembic upgrade head
→ village-insight-bootstrap
→ API / Worker / Web
```

`app.sh` 和全容器模式都自动执行该命令，因此本地快速开始仍保持三条命令。

## 安全与幂等

- 密码只从 `BOOTSTRAP_PASSWORD` 读取，至少 12 个字符；
- 已存在账号的密码不得被初始化命令覆盖；
- 缺失的租户、乡镇、村、账号、成员关系或范围可以补齐；
- 现有同名账号若绑定到其他租户或角色，初始化必须失败并要求人工处理，不得自动改绑；
- PostgreSQL 使用事务级 advisory lock，避免并发初始化；
- API 启动生命周期不再隐式创建账号。

## 验收

- 空 PostgreSQL 数据库迁移、初始化后，`admin` 和 `demo` 均能登录；
- `admin` 返回 `platform_admin`，`demo` 返回 `village_operator`；
- 重复执行不产生重复记录；
- 修改密码后重复执行不重置密码；
- 删除一个初始化账号及其关系后，重复执行可以补齐；
- `app.sh` 和 Compose 渲染结果均包含显式初始化步骤。

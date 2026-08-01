<p align="center">
  <img src="docs/assets/villageinsight-hero.png" alt="村知数：让每一份村情表格变成可追溯的可信数据" width="100%" />
</p>

# VillageInsight（村知数）

VillageInsight 是面向村情结构化资料的模板化解析、批量入库和可信问数平台。

项目坚持一条可审计的数据链路：

```text
文件物理证据
→ 开源解析能力生成结构候选
→ Hermes 结构与语义规划
→ 人工审核模板
→ 确定性写入 PostgreSQL
→ 指标化问数
```

## 当前能力

- FastAPI API；
- PostgreSQL 数据模型和 Alembic；
- PostgreSQL 租约任务队列；
- 独立 Worker；
- `.xlsx` 流式物理证据解析、`.xls`/`.csv` 适配；
- Hermes 直接 import 的适配边界；
- 批量文件上传和白名单服务端目录扫描；
- 字段注册、模板版本、审核事件和不可变导入计划；
- 精确/差异模板匹配和 Hermes 增量字段建议；
- PostgreSQL JSONB 权威记录、可重建类型化投影、单元格血缘和质量问题；
- 全量语料去重、布局聚类、可审核模板种子和幂等导入；
- React 用户工作台/管理端双 Shell、真实 URL 路由和页面级拆包；
- PostgreSQL 用户会话、租户成员、乡镇/村行政范围和后端强制授权；
- 可持久化的 LLM 提供商、模型、API Key 和输出上限配置页；
- Docker Compose 本地环境；
- pytest、Ruff、mypy、Vitest 和前端类型检查。

## 快速开始

首次启动前必须先准备两份互不提交 Git 的环境配置：

```bash
cp .env.example .env
cp docker/.env.example docker/.env
chmod 600 .env docker/.env
```

两份文件职责不同：

- 根目录 `.env`：API、Worker、前端构建预览服务、Hermes、上传目录和初始化账号配置；
- `docker/.env`：PostgreSQL 镜像、数据库身份、宿主机映射端口和资源参数。

启动前至少检查以下对应关系：

- `docker/.env` 中的 `POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`，必须与
  根目录 `.env` 的 `DATABASE_URL` 一致；
- 全容器模式还必须让 `docker/.env` 的 `POSTGRES_APPLICATION_URL` 使用相同的
  用户名、密码和数据库名，但主机名固定为 `postgres`、端口固定为 `5432`；
- `docker/.env` 的 `EXPOSE_POSTGRES_PORT` 必须与 `DATABASE_URL` 中的端口一致；
- 国内服务器无法拉取官方 PostgreSQL 镜像时，在 `docker/.env` 中设置经过验收的
  `POSTGRES_IMAGE`；
- 生产环境必须修改数据库密码、`BOOTSTRAP_PASSWORD`、可信来源和安全 Cookie 配置，
  不得直接使用示例凭据。

### 两种运行模式

项目支持“宿主机进程”和“全容器”两种运行模式，两者共用 PostgreSQL 数据结构，但
启动方式、前端服务和进程管理不同：

| 模块 | 宿主机进程模式 | 全容器模式 |
| --- | --- | --- |
| PostgreSQL | Docker Compose 容器 | Docker Compose 容器 |
| API | 宿主机 `uvicorn` 进程 | `api` 容器 |
| Worker | 宿主机三个独立 Worker 进程 | 三个独立 Worker 容器 |
| 前端 | 先构建静态文件，再由 Vite Preview 提供 | 构建静态文件后由 Nginx 容器提供 |
| 管理入口 | `app.sh` | `docker compose --profile application` |
| 主要用途 | 本地开发、联调、演示和单机验收 | 可重复构建的服务器容器化部署 |

根目录文件的职责如下：

- `app.sh`：宿主机进程模式的统一启动、后台运行、停止、状态和日志入口；它只把
  PostgreSQL 放在容器里，API、Worker 和构建后的前端预览服务都直接运行在宿主机；
- `compose.yaml`：全容器模式总入口，引入 PostgreSQL，并定义 API、三个 Worker、
  Web、网络、端口和持久化卷；
- `Dockerfile`：构建 API 和 Worker 共用的 Python 后端镜像；
- `frontend/Dockerfile`：构建前端资源并生成 Nginx Web 镜像；
- `Makefile`：对安装、数据库、迁移、测试和容器命令提供快捷入口，不是独立部署方案；
- `docker/docker-compose-base.yml`：PostgreSQL 基础服务、资源参数、健康检查和数据卷。

`app.sh` 现在会先执行 `npm run build`，再通过 Vite Preview 提供 `frontend/dist`，不再
直接运行 Vite 开发服务。但 Vite Preview 仍不是正式 Web 服务器，且 `app.sh` 也不是
systemd、Kubernetes 或容器编排器，因此当前仍应视为开发、联调和验收模式。生产部署
应以全容器模式为目标，并在切换前核对下文所述的镜像、凭据和持久化目录。

### 宿主机进程模式：推荐用于开发和验收

配置完成后，在项目根目录运行：

```bash
./app.sh
```

脚本会在后台启动 PostgreSQL、执行数据库迁移，并拉起 API、Worker 和前端。默认访问地址为
<http://localhost:9137>，监听地址和端口可通过环境变量覆盖：

```bash
HOST=0.0.0.0 PORT=9137 ./app.sh
```

应用生命周期和日志统一通过同一个脚本管理：

```bash
./app.sh status
./app.sh logs                 # 汇总跟踪全部日志
./app.sh logs api             # 只跟踪 API
./app.sh restart
./app.sh stop                 # 保留 PostgreSQL
./app.sh foreground           # 前台调试模式
```

运行 PID 保存在 `data/run/`，持久日志保存在 `logs/`。日志会在启动前按大小做有限轮转。

### 日志与排障

不确定错误来自哪个组件时，先运行 `./app.sh status`，再用 `./app.sh logs` 汇总跟踪
全部日志。也可以根据现象只查看对应组件：

| 问题现象 | 查看命令 | 日志文件 |
| --- | --- | --- |
| 启动失败、前端构建失败、迁移失败、端口冲突 | `./app.sh logs supervisor` | `logs/supervisor.log` |
| 接口 500、登录或页面请求失败 | `./app.sh logs api` | `logs/api.log` |
| 文件解析或模板匹配失败 | `./app.sh logs worker-parse` | `logs/worker-parse.log` |
| Hermes 识别或模型调用失败 | `./app.sh logs worker-hermes` | `logs/worker-hermes.log` |
| 正式入库或 JSONB 物化失败 | `./app.sh logs worker-materialize` | `logs/worker-materialize.log` |
| 构建后前端预览服务或静态资源问题 | `./app.sh logs frontend` | `logs/frontend.log` |

快速检索已有日志中的常见错误：

```bash
rg -n "ERROR|Traceback|Exception|失败" logs/
```

日志默认在应用启动前按单文件 20MB 轮转并保留 5 份，可通过 `LOG_MAX_BYTES` 和
`LOG_BACKUP_COUNT` 调整。日志只记录任务 ID、错误类型和安全的运行信息，不得新增原始
身份证号、银行卡号、手机号或人员姓名等敏感值。

为兼容已有本地开发流程，`.env` 缺失时脚本仍会从 `.env.example` 创建，但
`docker/.env` 不会自动生成；新机器不应依赖自动创建，应在启动前按上述步骤配置并核对
两份文件。Hermes 默认关闭；需要启用时，请先在 `.env` 中配置相应模型和密钥。

开发环境首次启动会按 `.env` 中的 `BOOTSTRAP_*` 配置创建一个业务租户、一个村和
两个固定职责账号：

- `tenant-admin`：可在租户范围问答，并选择下属村上传；
- `village-operator`：可以上传并查询所属村；

`.env.example` 中的密码仅用于本地首次启动，启动前必须修改。已有 `.env` 的开发环境
需要手工补充 `BOOTSTRAP_TENANT_NAME`、`BOOTSTRAP_TOWNSHIP_NAME`、
`BOOTSTRAP_VILLAGE_NAME` 和 `BOOTSTRAP_PASSWORD`。生产环境不得使用示例凭据。

需要创建管理员租户、X租户、六个村级测试账号、租户管理员 `x` 和平台管理员 `admin`
时，执行：

```bash
uv run alembic upgrade head
uv run village-insight-demo-identities
```

该命令只补齐或修复测试组织关系，不会重置用户已修改的密码。账号清单见
[`docs/多租户行政区划与数据权限实施方案.md`](docs/多租户行政区划与数据权限实施方案.md)；
生产环境禁止运行。

宿主机进程模式也可以分别启动各组件：

```bash
cp .env.example .env
cp docker/.env.example docker/.env
# 编辑并核对两份配置后再继续
docker compose --env-file docker/.env up -d postgres
uv run alembic upgrade head
```

这一步只启动 PostgreSQL。

### 全容器模式：服务器部署基础

需要以容器方式启动整个应用时：

```bash
docker compose --env-file docker/.env --profile application up --build
```

API 和三个 Worker 共用 `docker/.env` 中的 `POSTGRES_APPLICATION_URL`，不再在
`compose.yaml` 内分别硬编码数据库账号。该连接使用容器网络地址 `postgres:5432`；
`app.sh` 和 Alembic 等宿主机命令继续读取根目录 `.env` 的 `DATABASE_URL`。

#### PostgreSQL 镜像来源

默认使用 `postgres:17-alpine`。官方仓库不可达时，可在 `docker/.env` 切换到经过验收
的轩辕镜像：

```dotenv
POSTGRES_IMAGE=uhej0txoqzpd9n.xuanyuan.run/postgres:17-alpine
```

首次拉取后核对 PostgreSQL 版本和仓库摘要：

```bash
docker pull uhej0txoqzpd9n.xuanyuan.run/postgres:17-alpine
docker run --rm uhej0txoqzpd9n.xuanyuan.run/postgres:17-alpine postgres --version
docker image inspect --format '{{json .RepoDigests}}' \
  uhej0txoqzpd9n.xuanyuan.run/postgres:17-alpine
```

生产验收完成后，将输出的完整 `仓库地址@sha256:...` 写入 `POSTGRES_IMAGE`，避免同名
tag 后续漂移。切换镜像源不会自动验证其内容与官方镜像一致，必须保留版本和摘要记录。

#### PostgreSQL 数据目录

`POSTGRES_DATA_DIR` 留空时继续使用命名卷 `village_insight_postgres_data`，适合开发和
临时验收；生产环境可以配置绝对路径：

```dotenv
POSTGRES_DATA_DIR=/opt/village-insight/data/postgres/data
```

Compose 会把该目录绑定到容器 `/var/lib/postgresql/data`。首次启动前必须根据所用镜像
中 `postgres` 用户的 UID/GID 创建目录并设置权限；不要依赖 Docker 自动创建
root 属主目录。可以先检查镜像身份：

```bash
docker run --rm --entrypoint id "镜像完整名称" postgres
```

**已有命名卷时不能直接填写一个空的 `POSTGRES_DATA_DIR` 后重启。** 空目录会被初始化
为全新数据库集群，原命名卷数据不会自动复制，也不是数据被 PostgreSQL 删除。切换前
必须完成以下门禁：

1. 停止业务写入并生成 `pg_dump -Fc` 或完整服务器迁移包；
2. 校验备份非空、SHA-256、`pg_restore --list` 和源文件/密钥清单；
3. 停止旧 PostgreSQL，准备目标目录属主、权限和磁盘空间；
4. 配置 `POSTGRES_DATA_DIR`，启动新集群并恢复备份；
5. 执行 `uv run alembic upgrade head`，再做登录、批次、问数和记录数对账；
6. 验收完成前保留原命名卷，不得删除或覆盖。

数据库整库备份必须放在与 PGDATA 不同的故障域。当前不挂载
`/docker-entrypoint-initdb.d` 或容器 `/backups`：数据库结构由 Alembic 管理，备份仍由
下文的受审计迁移工具管理。项目根目录 `backups/four-layer-pre-restore/` 只是模板恢复前
快照，不是 PostgreSQL 整库备份。

当前唯一外部基础组件是 PostgreSQL。首期不启动 Redis/Valkey、MinIO、
Elasticsearch/OpenSearch/Infinity、MySQL、Neo4j 或独立 Hermes Gateway。
批量任务队列使用 PostgreSQL；上传文件使用挂载卷；Hermes 安装在应用 Python
环境内。出现经过压测证明的瓶颈后，再单独评估 Redis 或对象存储。

### PostgreSQL 服务器容量与参数

PostgreSQL 参数通过 `docker/.env` 覆盖，Compose 中的默认值面向单机批量入库：
`shared_buffers=512MB`、`work_mem=4MB`、`max_wal_size=1GB`、
`min_wal_size=256MB`、`wal_compression=on`。这些值避免并发连接累计占用过多
内存，并缩短大批量写入后的 WAL 恢复窗口；生产服务器应在压测后调整，而不是
直接按主机总内存放大 `work_mem`。

数据卷必须放在独立、可监控的磁盘上，数据库备份不要保存在同一数据卷。建议按
“当前数据库与 WAL 占用的 2 倍，再额外保留至少 10GB”规划容量。健康检查除了
`pg_isready`，还要求 `PGDATA` 至少保留 `POSTGRES_MIN_FREE_DISK_MB`（默认
4096MB）；空间不足时 PostgreSQL 会标记为 `unhealthy`，应用启动流程不会继续
启动 Worker。服务器监控还应对磁盘使用率 80%/90% 设置告警，因为
`max_wal_size` 是检查点目标值，不是 WAL 的绝对硬上限。

推荐在服务器的 `docker/.env` 中按资源等级覆盖：

```dotenv
# 8GB 内存、独立 SSD 数据盘的起始配置
POSTGRES_SHARED_BUFFERS=1GB
POSTGRES_EFFECTIVE_CACHE_SIZE=4GB
POSTGRES_WORK_MEM=4MB
POSTGRES_MAINTENANCE_WORK_MEM=256MB
POSTGRES_MAX_WAL_SIZE=2GB
POSTGRES_MIN_WAL_SIZE=512MB
POSTGRES_MIN_FREE_DISK_MB=10240
```

修改参数后使用 `docker compose --env-file docker/.env up -d --force-recreate
postgres` 重建容器；该命令不会删除命名卷或 bind mount 数据，但修改
`POSTGRES_DATA_DIR` 会让容器看到不同的数据目录，必须先遵守上述迁移门禁。先等待容器
`healthy`，再启动 API 和 Worker。

容器部署默认访问：

- Web：<http://localhost:5173>
- API：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

本机开发：

```bash
uv sync --all-extras
uv run alembic upgrade head
uv run uvicorn village_insight.api.app:app --reload
```

另一个终端运行 Worker：

```bash
uv run village-insight-worker
```

前端：

```bash
cd frontend
npm install
npm run dev
```

`./app.sh` 本机开发默认访问 Web <http://localhost:9137>、API
<http://localhost:9138>。登录后的路由由后端返回的固定角色决定：村级数据员使用
`/batches`、`/questions`；租户管理员使用相同入口但可以选择本租户任意村；平台
管理员进入 `/admin/access`、`/admin/reviews`、`/admin/records`、
`/admin/catalog` 和 `/admin/settings`。

## 备份与服务器迁移

### 四层模板应急恢复

四层模板采用 Git 跟踪的具名完整恢复包。恢复包包含模板对象、全部版本、字段变体、
组合槽位和审核事件，不包含用户、上传文件、批次、JSONB 业务记录或问答数据。先列出
并校验已有恢复点：

```bash
./scripts/restore-four-layer-baseline.sh --list
```

默认恢复点是经过验收的 `current-205-expanded`。任何正式恢复都应先执行 dry-run：

```bash
./scripts/restore-four-layer-baseline.sh --dry-run
./scripts/restore-four-layer-baseline.sh --baseline current-205-expanded --dry-run
```

dry-run 会逐表显示待补回、待修复和待清理的模板行，再显示发布状态变化。确认预览后
执行交互恢复；工具会要求输入恢复点名称和完整包摘要，并在切换前自动保存当前完整
模板目录：

```bash
./scripts/restore-four-layer-baseline.sh --baseline current-205-expanded
```

恢复在一个数据库事务中补回缺失的基线模板、修复被篡改的基线内容、恢复变体和槽位，
最后切换发布版本。基线之后的模板版本和审核事件继续保留，但不再参与匹配；业务表不在
写入范围内。恢复点清单及校验值位于 `config/four-layer-recovery-baselines.json`，完整
制品位于 `recovery/four-layer-baselines/`。工具同时校验文件 SHA-256、包逻辑摘要、
四层数量和 GitHub 100MB 单文件上限，禁止按目录日期自动选择所谓“最新”备份。

新环境从零初始化不需要数据文件：`docker compose --env-file docker/.env up -d postgres`
后执行
`uv run alembic upgrade head` 即可建出全部表结构，首次启动再按 `BOOTSTRAP_*`
自动创建初始租户和账号。

需要把现有数据整体迁移到服务器时，使用 PostgreSQL 全量备份：

```bash
# 备份（本机）
docker exec village-insight-postgres-1 pg_dump -U village_insight -d village_insight \
  -Fc -f /tmp/village_insight.dump
docker cp village-insight-postgres-1:/tmp/village_insight.dump ./backups/

# 恢复（服务器先执行 docker compose --env-file docker/.env up -d postgres）
docker cp ./backups/village_insight.dump village-insight-postgres-1:/tmp/restore.dump
docker exec village-insight-postgres-1 pg_restore -U village_insight -d village_insight \
  --clean --if-exists --no-owner --no-privileges /tmp/restore.dump
```

备份中已包含 `alembic_version`，启动时 `alembic upgrade head` 会自动对齐后续迁移。
除数据库外，完整业务迁移还必须带上数据库实际引用的原始文件和
`SECRET_KEY_PATH`（默认 `./data/secrets/settings.key`）。不要盲目复制整个上传卷：
`scripts/create-server-transfer-bundle.py` 会从 PostgreSQL 反向收集入库文件和模板证据引用，
按 SHA-256 去重后生成项目内迁移包；`scripts/verify-server-transfer-bundle.py`
负责在删除目标库之前做离线完整性校验。

服务器最终将历史源文件放在项目内
`data/server-transfer/current/`，通过只读清单解析数据库中保留的旧绝对路径；
新上传文件仍使用 `village_insight_upload_data`。设置页保存的 LLM API Key
依赖原 `settings.key` 解密，迁移时不得用新随机密钥覆盖。完整操作顺序和验收门禁见
[`docs/服务器整库迁移/实施方案.md`](docs/服务器整库迁移/实施方案.md)。

## 验证

```bash
make check
```

## 合成导入与问数样例

仓库内置 [合成村情数据集](sample-data/synthetic-village-v1/README.md)，包含 300 条完全
合成记录和 231 道问数金标，可用于导入、模板匹配、物化和问数演练。生成器会
连接 PostgreSQL 校验指定的已发布四层模板，两个数据文件必须精确命中固定的 Region、
Sheet 和 Workbook Route，不能以近似模板替代。所有姓名、村名、证件、电话和地址均为
显式测试值，样例应只导入专用测试租户和村级范围。

样例固定放在仓库根部 `sample-data/`，不放入已被忽略的 `docs/` 或运行时 `data/`。
`.gitignore` 使用 `!sample-data/` 和 `!sample-data/**` 明确保留该目录。

### 1. 导入四层模板

以下操作应连接测试 PostgreSQL。先完成数据库迁移，再检查恢复包并预演：

```bash
uv run alembic upgrade head
./scripts/restore-four-layer-baseline.sh --list
./scripts/restore-four-layer-baseline.sh \
  --baseline current-205-expanded --dry-run
```

确认目标库和预演变更后，执行模板导入。日常人工操作保留交互确认；只有一次性隔离库或
CI 才使用 `--yes`：

```bash
./scripts/restore-four-layer-baseline.sh \
  --baseline current-205-expanded
```

该步骤只导入和恢复四层模板目录，不会导入样例记录。脚本读取当前 `.env` 或进程环境中
的 `DATABASE_URL`，执行前必须确认它指向测试库。

### 2. 验证样例受模板覆盖

```bash
uv run python -m village_insight.synthetic_dataset validate \
  --output-directory sample-data/synthetic-village-v1 \
  --report sample-data/synthetic-village-v1/template-coverage-report.json
```

命令必须返回 `accepted: true`。户籍人口文件应为字段 9/9 exact，党员名册应为字段
13/13 exact；两者的 Region、Sheet、Workbook Route 都必须命中清单指定的 ID 和版本，
并且 `requires_hermes` 为 `false`。

### 3. 导入样例数据

```bash
./app.sh start
./app.sh status
```

登录后进入“批次”，创建或选择专用测试租户下的村级单元“演示一村”，点击“新建导入”
并选择“批量上传”，一次上传：

- `sample-data/synthetic-village-v1/data/演示一村户籍人口.xlsx`
- `sample-data/synthetic-village-v1/data/演示一村党员名册.xlsx`

批次名称可填写“演示一村合成数据验收”。等待两个文件状态完成后，文件详情中不应出现
Hermes 复核；正式记录数应分别为 180 和 120，总计 300。不要把样例上传到真实村的
业务范围。

### 4. 测试导入和问数

进入“问题”，选择刚才的测试租户、演示一村和这两个文件。测试题位于
`sample-data/synthetic-village-v1/questions.xlsx`；机器可比对值位于
`questions.json` 和 `expected-results.json`。最小人工冒烟测试为：

- “演示一村户籍人口表共有多少人？”应答 `180人`；
- “演示一村户籍人口表共有多少户？”应答 `60户`；
- “演示一村党员名册共有多少人？”应答 `120人`；
- 请求身份证或电话号码时应拒绝返回直接敏感标识符。

代码与制品回归：

```bash
uv run pytest tests/test_synthetic_dataset.py \
  tests/test_question_benchmark_classification.py -q
uv run pytest -q
if git check-ignore -q sample-data/synthetic-village-v1/manifest.json; then
  echo "错误：sample-data 被 Git 忽略"
  exit 1
fi
```

最后一个检查正常时不输出文件路径。提交前还应确认 `git status --short sample-data/`
显示样例文件为待提交或已跟踪状态。

## 文档

- [结构化文档入库与问数平台实施方案](docs/结构化文档入库与问数平台实施方案.md)
- [多租户、行政区划与数据权限实施方案](docs/多租户行政区划与数据权限实施方案.md)
- [开发约定](docs/development.md)
- [Hermes 内嵌运行说明](docs/hermes-embedded.md)

## Hermes 说明

业务代码只依赖 `HermesRuntime` 接口。运行时通过内部固定版本 wheel 提供：

```python
from run_agent import AIAgent
```

默认关闭 Hermes。`hermes-agent==0.19.0` 已作为固定 Python 依赖安装到
API/Worker 环境，由程序直接 `from run_agent import AIAgent`，不启动 Gateway。
可运行 `uv run village-insight-hermes-check` 检查安装。本地模型使用
SiliconFlow 托管的 DeepSeek V4：普通请求使用
`deepseek-ai/DeepSeek-V4-Flash`，需要思考的疑难请求使用
`deepseek-ai/DeepSeek-V4-Pro`。密钥可以通过环境变量注入，也可以在设置页加密
保存；业务代码通过调用策略选择模型。

## 真实文件验收状态

按修订后的实施门禁，真实业务文件闭环只能在阶段 0—6 的方案实现完成后开始。
旧版本做过的语料分析和技术运行不能替代新的端到端验收，也不能声明业务基线通过。

当前已完成首个实施批次：JSONB 权威记录、导入来源、证据/正式状态分离、
用户/管理双 Shell 和真实 Chromium 回归。阶段 2、3 仍有明确未完成项，详见：

- [阶段 2 中间验收](docs/research/stage-05-materialization/ACCEPTANCE-2026-07-29.md)
- [阶段 3 中间验收](docs/research/stage-03-batch-review/ACCEPTANCE-2026-07-29.md)

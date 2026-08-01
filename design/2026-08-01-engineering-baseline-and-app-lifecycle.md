# VillageInsight 第一阶段工程治理实施方案

日期：2026-08-01

## 目标

本阶段解决当前工程基线中已经确认、且不改变业务语义的五类问题：

1. `app.sh` 默认只能前台运行，缺少可靠的后台启动、停止、状态和日志入口；
2. 大部分 Python 测试被 `.gitignore` 排除，导致验证基线无法随仓库迁移；
3. 仓库缺少持续集成门禁；
4. 后端 Docker 构建上下文过大，且未使用 `uv.lock` 冻结依赖；
5. 前端存在 React Hook 依赖警告，而 `make check` 未执行 ESLint。

Nginx 配置不属于本阶段范围，保持现状。

## 一、应用生命周期与日志

### 对外命令

```bash
./app.sh                 # 等同于 start，后台启动并等待 API 就绪
./app.sh start
./app.sh foreground      # 前台调试
./app.sh stop
./app.sh restart
./app.sh status
./app.sh logs [all|api|worker-parse|worker-hermes|worker-materialize|frontend|supervisor]
```

### 运行状态

- PID 文件写入 `data/run/`，不进入版本库；
- 日志写入 `logs/`，不进入版本库；
- supervisor 负责 API、三个 Worker 和 Vite 的完整生命周期；
- `stop` 只停止 PID 文件证明属于本次启动的进程，不按进程名或端口杀死其他项目；
- PostgreSQL 继续由 Compose 管理，应用停止时不停止 PostgreSQL；
- 重复启动返回当前状态，不创建重复 Worker；
- 端口被未登记进程占用时明确报错，由操作者确认来源。

### 日志约定

分别保留 `supervisor.log`、`api.log`、三个 Worker 日志和 `frontend.log`。启动前按大小做有限轮转，默认单文件 20MB、保留 5 份，避免长期运行无限增长。日志不得新增原始身份证号、银行卡号、手机号或姓名输出。

## 二、版本库基线

- 提交全部 `tests/test_*.py` 测试源码；
- 按项目约定继续整体忽略 `docs/`，其中的工程记录、运行报告和业务资料均保持本地；
- 根目录 `design/` 用于存放需要随代码版本化的正式实施方案；
- 继续忽略真实 Excel、JSON 运行报告、截图、检查点和业务数据。

本次只让测试源码进入版本控制，不自动提交或改写 `docs/` 内容。

## 三、持续集成

新增 GitHub Actions：

- Python 3.13、Node 22 和 PostgreSQL 17；
- `uv sync --all-extras --frozen`；
- Ruff、严格 mypy、pytest；
- 在真实 PostgreSQL 上执行 `alembic upgrade head`；
- ESLint 零警告、TypeScript、Vitest 和 Vite build；
- 校验 Compose 配置和 `app.sh` Bash 语法。

本阶段不把依赖外部模型或真实业务文件的 E2E 回归放入普通提交门禁。

## 四、Docker 构建

- 新增根目录 `.dockerignore`，排除 `.git`、虚拟环境、Node 依赖、数据、日志、测试产物、外部参考项目和未参与镜像构建的文档；
- 后端镜像复制 `uv.lock`，使用固定版本 `uv` 和 `uv sync --frozen --no-dev`；
- 保持非 root 运行以及现有 API/Worker 共用镜像方式；
- Nginx 镜像及其配置不在本阶段调整。

## 五、React 工程门禁

- 使用 `useCallback` 或稳定的派生值修复 Effect 缺失依赖；
- 将仅供测试复用的纯函数移出页面组件文件，消除 Fast Refresh 警告；
- 不通过 `eslint-disable` 隐藏旧闭包风险；
- `npm run lint` 使用 `--max-warnings=0`，并纳入 `make check`。

## 验收标准

1. `./app.sh start` 成功后命令返回，关闭终端不影响应用；
2. `status` 能报告 supervisor、各组件和 live/ready；
3. `logs` 能查看各组件持久日志；
4. `stop` 完整停止应用进程但保留 PostgreSQL；
5. Ruff、mypy、pytest、ESLint、类型检查、Vitest、构建全部通过；
6. Alembic 保持单一 head，并能在 PostgreSQL 空库升级到 head；
7. `git status` 能看到应纳入版本库的测试源码，且 `docs/` 和业务数据文件保持忽略；
8. Nginx 文件没有修改。

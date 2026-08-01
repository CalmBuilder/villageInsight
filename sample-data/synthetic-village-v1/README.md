# 合成村情导入与问数样例

这是一套可公开随 Git 交付的完全合成数据，不含真实姓名、身份证号、手机号、村名或
地址。数据文件不是对原始业务文件脱敏后复制，而是由固定规则重新生成。

## 内容

- `data/演示一村户籍人口.xlsx`：180 人、60 户。
- `data/演示一村党员名册.xlsx`：120 人。
- `questions.xlsx`：兼容现有题库加载器的中文测试题。
- `questions.json`：包含题目类别、参考文件、语义字段和机器可比对金标。
- `expected-results.json`：按 `case_id` 提供预期值和比较方式。
- `manifest.json`：记录模板版本、数量、SHA-256 和隐私声明。
- `template-coverage-report.json`：真实匹配器生成的四层模板覆盖报告。

身份证和电话列使用 `TEST-ID-*`、`TEST-PHONE-*`，它们是显式测试标识符，不是可验证
身份证号或可拨打手机号。敏感问题的金标是“应拒绝”，不会提供标识符内容。

## 存放与 Git 跟踪

样例固定存放在仓库根部 `sample-data/synthetic-village-v1/`，不放在 `docs/` 或运行时
`data/`。根目录 `.gitignore` 已通过 `!sample-data/` 和 `!sample-data/**` 明确保留，
因此整个样例目录应随 Git 提交。

检查是否被忽略：

```bash
git check-ignore sample-data/synthetic-village-v1/manifest.json
git status --short sample-data/
```

第一条命令正常时没有输出且退出码为 1；第二条应显示未提交或已跟踪的样例文件。

## 1. 导入模板

先确认 `.env` 或进程环境中的 `DATABASE_URL` 指向专用测试库，然后迁移数据库、检查
恢复点并预演模板导入：

```bash
uv run alembic upgrade head
./scripts/restore-four-layer-baseline.sh --list
./scripts/restore-four-layer-baseline.sh \
  --baseline current-205-expanded --dry-run
```

确认无误后执行：

```bash
./scripts/restore-four-layer-baseline.sh \
  --baseline current-205-expanded
```

恢复脚本只导入四层模板，不包含以下样例业务记录。隔离库和 CI 可以显式增加 `--yes`，
日常操作应保留交互确认。

## 2. 验证模板覆盖

```bash
uv run python -m village_insight.synthetic_dataset validate \
  --output-directory sample-data/synthetic-village-v1 \
  --report sample-data/synthetic-village-v1/template-coverage-report.json
```

必须得到 `accepted: true`。户籍人口应为 9/9 字段 exact，党员名册应为 13/13 字段
exact；两个文件的 Region、Sheet、Workbook Route 均应精确命中指定 ID 和版本，且
`requires_hermes=false`。

## 3. 启动测试应用（不会导入样例数据）

启动并确认应用正常：

```bash
./app.sh start
./app.sh status
```

`app.sh` 只启动应用、迁移数据库结构并初始化登录账号，不读取本目录，也不会自动导入
任何样例业务记录。应用每次重启都不会重复导入样例数据。

## 4. 手工导入样例数据（仅限测试环境）

使用 `demo` 登录后进入“批次”，账号默认绑定专用演示租户下的“演示一村”。点击
“新建导入”和“批量上传”，一次选择 `data/演示一村户籍人口.xlsx`、
`data/演示一村党员名册.xlsx`，然后开始自动入库。不要把样例导入真实村的业务范围，
生产环境不得执行本节操作。

处理完成后，两个文件都不应进入 Hermes 复核；正式记录数应分别为 180 和 120，总计
300。

## 5. 测试导入与问数

进入“问题”，选择测试租户、演示一村及刚导入的两个文件。`questions.xlsx` 用于人工
逐题测试，`questions.json` 和 `expected-results.json` 用于按 `case_id` 自动对账。
至少验证：

- 户籍人口总人数为 180；
- 户数为 60；
- 党员名册人数为 120；
- 身份证、电话号码问题按 `contains_direct_sensitive_identifier` 阻断。

运行仓库回归：

```bash
uv run pytest tests/test_synthetic_dataset.py \
  tests/test_question_benchmark_classification.py -q
uv run pytest -q
```

## 重新生成

重新生成和检查：

```bash
uv run python -m village_insight.synthetic_dataset generate \
  --output-directory sample-data/synthetic-village-v1
uv run python -m village_insight.synthetic_dataset validate \
  --output-directory sample-data/synthetic-village-v1 \
  --report sample-data/synthetic-village-v1/template-coverage-report.json
```

生成器会先从当前 `.env` / 环境配置连接 PostgreSQL，并验证指定模板仍处于发布状态且
表头、字段绑定和组合槽位没有漂移。模板不一致时会直接失败，不会生成近似文件。

`questions.xlsx` 可用于人工问数演练，自动回归应以 `questions.json` 的 `case_id` 和
`expected-results.json` 为准。数值题要求精确相等；`comparison=policy` 的题目要求按
`expected_reason_code` 阻断。

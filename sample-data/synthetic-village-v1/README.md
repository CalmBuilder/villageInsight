# 合成村情数据集

这是一套随 Git 交付的完全合成数据，不含真实姓名、身份证号、手机号、村名或地址。
数据不是从真实业务文件脱敏复制，而是由固定规则重新生成。

首次启动、恢复四层模板、上传样例和问数冒烟的完整主流程见
[项目 README](../../README.md)。本文件只说明样例制品、验收契约和重新生成方法，避免
重复维护启动步骤。

## 制品

- `data/演示一村户籍人口.xlsx`：180 人、60 户；
- `data/演示一村党员名册.xlsx`：120 人；
- `questions.xlsx`：231 道中文测试题；
- `questions.json`：题目类别、参考文件、语义字段和机器可比对金标；
- `expected-results.json`：按 `case_id` 提供预期值和比较方式；
- `manifest.json`：模板版本、数量、SHA-256 和隐私声明；
- `template-coverage-report.json`：真实匹配器生成的四层模板覆盖报告。

身份证和电话列使用 `TEST-ID-*`、`TEST-PHONE-*`，它们是显式测试标识符，不是可验证
身份证号或可拨打手机号。敏感问题的金标要求拒绝，不提供标识符内容。

## 验收契约

- 户籍人口文件必须精确命中指定 Region、Sheet、Workbook Route 和 9/9 字段；
- 党员名册必须精确命中指定 Region、Sheet、Workbook Route 和 13/13 字段；
- 两个文件都必须为 `requires_hermes=false`；
- 正式记录数必须分别为 180 和 120，总计 300；
- 户籍人口、户数、党员人数分别为 180、60、120；
- 身份证和电话号码问题必须按策略阻断。

检查模板覆盖：

```bash
uv run python -m village_insight.synthetic_dataset validate \
  --output-directory sample-data/synthetic-village-v1 \
  --report sample-data/synthetic-village-v1/template-coverage-report.json
```

结果必须包含 `accepted: true`，不能用近似模板替代。

## 自动回归

```bash
uv run pytest tests/test_synthetic_dataset.py \
  tests/test_question_benchmark_classification.py -q
```

`questions.xlsx` 用于人工演练；自动回归以 `questions.json` 的 `case_id` 和
`expected-results.json` 为准。数值题要求精确相等，`comparison=policy` 的题目按
`expected_reason_code` 验证阻断。

确认样例仍被 Git 跟踪：

```bash
if git check-ignore -q sample-data/synthetic-village-v1/manifest.json; then
  echo "错误：sample-data 被 Git 忽略"
  exit 1
fi
git status --short sample-data/
```

## 重新生成

重新生成会连接当前 `.env` 或进程环境指定的 PostgreSQL，并验证目标四层模板仍处于发布
状态且绑定未漂移。模板不一致时命令会失败，不生成近似文件。

```bash
uv run python -m village_insight.synthetic_dataset generate \
  --output-directory sample-data/synthetic-village-v1
uv run python -m village_insight.synthetic_dataset validate \
  --output-directory sample-data/synthetic-village-v1 \
  --report sample-data/synthetic-village-v1/template-coverage-report.json
```

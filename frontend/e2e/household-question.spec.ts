import { expect, test } from "@playwright/test";

const question = process.env.E2E_HOUSEHOLD_QUESTION;
const expectedAnswer = process.env.E2E_HOUSEHOLD_ANSWER;
const secondExpectedAnswer = process.env.E2E_HOUSEHOLD_SECOND_ANSWER;
const sourceFile = process.env.E2E_SOURCE_FILE;

test("户号问题通过结构化字段返回户主", async ({ page }) => {
  test.setTimeout(900_000);
  test.skip(!question || !expectedAnswer, "需要显式指定户号问题和预期答案");
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (
      message.type() === "error"
      && !message.text().includes("401 (Unauthorized)")
    ) {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await page.getByLabel("用户名").fill(
    process.env.E2E_TENANT_ADMIN_USERNAME ?? "x",
  );
  await page.getByLabel("密码").fill(
    process.env.E2E_TENANT_ADMIN_PASSWORD ?? "demo",
  );
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("link", { name: /可信问数/ }).click();
  if (sourceFile) {
    const sourcePanel = page.getByLabel("问数文件范围与查询凭据");
    await sourcePanel.getByLabel("搜索问数文件").fill(sourceFile);
    await sourcePanel.getByRole("button", { name: "查找" }).click();
    const sourceOption = sourcePanel.getByRole("radio", {
      name: new RegExp(sourceFile.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    });
    await expect(sourceOption).toBeVisible();
    await sourceOption.click();
    await expect(sourceOption).toHaveAttribute("aria-checked", "true");
  } else {
    await page
      .getByLabel("问数文件范围与查询凭据")
      .getByRole("radio", { name: /默认有效文件/ })
      .click();
  }
  await page.getByRole("button", { name: "新建问数会话" }).click();

  const input = page.getByLabel("继续提问");
  await expect(input).toBeVisible();
  await input.fill(question!);
  await page.getByRole("button", { name: "查询村情" }).click();

  const latestAnswer = page.locator(".question-response-part--answer").last();
  await expect(latestAnswer).toContainText(expectedAnswer!, { timeout: 840_000 });
  if (secondExpectedAnswer) {
    await expect(latestAnswer).toContainText(secondExpectedAnswer);
  }
  await expect(latestAnswer).not.toContainText("unclassified_record");
  await expect(latestAnswer).not.toContainText("查不到");
  await expect(latestAnswer).not.toContainText("推断");
  expect(browserErrors).toEqual([]);
});

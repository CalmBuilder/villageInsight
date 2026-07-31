import { expect, test } from "@playwright/test";

test("租户管理员按村切换问数范围且过程与回答分区", async ({ context, page }) => {
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

  const scopeSelect = page.getByLabel("查询范围");
  await expect(scopeSelect).toBeVisible();
  await expect(scopeSelect).toHaveValue(/.+/);
  const optionCount = await scopeSelect.locator("option").count();
  const allVillagesLabel = await scopeSelect.locator("option").first().textContent();
  const villageCount = Number(allVillagesLabel?.match(/全部村（(\d+)个）/)?.[1] ?? -1);
  expect(villageCount).toBeGreaterThan(0);
  expect(optionCount).toBe(villageCount + 1);

  await expect(page.getByText("数据核对", { exact: true }).first()).toBeVisible();
  await expect(
    page.locator(".question-response-part--process summary small").first(),
  ).toContainText(/耗时 \d+(分( \d+秒)?|秒)/);
  await expect(page.getByText("回答", { exact: true }).first()).toBeVisible();
  const answerSection = page.locator(".question-response-part--answer").first();
  await expect(answerSection).not.toContainText("internal analysis");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const firstQuestion = page.locator(".question-bubble--user").first();
  await page.getByRole("button", { name: "复制问题" }).first().click();
  await expect(page.getByRole("button", { name: "已复制" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    await firstQuestion.textContent(),
  );
  await expect(page.getByRole("button", { name: "复制答案" }).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^(重新查询|重试查询)$/ }),
  ).toHaveCount(1);
  await page.screenshot({
    path: "../test-results/question-message-actions.png",
    fullPage: true,
  });

  const createResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/questions/conversations")
      && response.request().method() === "POST",
  );
  await scopeSelect.selectOption({ label: "官庄村" });
  await page.getByRole("button", { name: "新建问数会话" }).click();
  const created = await (await createResponse).json() as {
    scope_name: string;
    scope_mode: string;
    scope_unit_id: string;
  };
  expect(created.scope_name).toBe("官庄村");
  expect(created.scope_mode).toBe("village");
  expect(created.scope_unit_id).toBe(await scopeSelect.inputValue());
  await expect(page.getByText("智能理解问题 · 按当前范围作答")).toBeVisible();

  expect(browserErrors).toEqual([]);
});

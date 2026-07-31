import { expect, test } from "@playwright/test";

test("模型连接必须测试通过后才能保存并立即生效", async ({ page }) => {
  test.setTimeout(150_000);
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
    process.env.E2E_GOVERNOR_USERNAME ?? "admin",
  );
  await page.getByLabel("密码").fill(
    process.env.E2E_GOVERNOR_PASSWORD ?? process.env.E2E_PASSWORD ?? "admin",
  );
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page).toHaveURL(/\/admin\/reviews$/);
  await page.goto("/admin/settings");

  await expect(page.getByRole("heading", { name: "选择供应商，测试后启用" })).toBeVisible();
  await expect(page.getByLabel("快速识别模型")).toHaveValue("deepseek-v4-flash");
  await expect(page.getByLabel("深度推理模型")).toHaveValue("deepseek-v4-pro");
  await expect(page.getByLabel("思考参数")).toHaveValue("deepseek");

  await expect(page.getByRole("button", { name: "保存并启用" })).toBeDisabled();

  const testResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/settings/llm/test")
      && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "测试当前配置" }).click();
  expect((await testResponse).ok()).toBeTruthy();
  await expect(page.getByText(/连接正常 · deepseek-v4-flash/)).toBeVisible();

  const saveResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/settings/llm")
      && response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "保存并启用" }).click();
  expect((await saveResponse).ok()).toBeTruthy();
  await expect(page.getByText("配置已加密保存并立即生效")).toBeVisible();
  expect(browserErrors).toEqual([]);
});

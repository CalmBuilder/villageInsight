import { expect, test } from "@playwright/test";

test("清空测试数据后可以从前端开始低并发批量入库", async ({ page }) => {
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

  await expect(page.getByRole("heading", { name: "文件入库" })).toBeVisible();
  const fileResponse = await page.request.get("/api/files?limit=20&offset=0");
  expect(fileResponse.ok()).toBeTruthy();
  const filePage = await fileResponse.json() as {
    total: number;
    items: unknown[];
  };
  expect(filePage.total).toBe(0);
  expect(filePage.items).toEqual([]);

  await page.getByRole("button", { name: /处理能力/ }).click();
  const capacity = page.locator('[aria-label="后台处理能力"]');
  await expect(capacity).toContainText("结构解析");
  await expect(capacity).toContainText("0 / 2");
  await expect(capacity).toContainText("AI 辅助");
  await expect(capacity).toContainText("正式入库");

  await page.getByRole("button", { name: "＋ 导入文件" }).click();
  const drawer = page.getByRole("complementary", { name: "导入结构化文件" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByLabel("所属村")).toBeVisible();
  const fileInput = drawer.getByLabel("选择结构化文件");
  await expect(fileInput).toHaveAttribute("multiple", "");
  expect(await fileInput.evaluate((element: HTMLInputElement) => element.multiple))
    .toBe(true);
  expect(browserErrors).toEqual([]);
});

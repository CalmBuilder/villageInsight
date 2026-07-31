import { expect, test } from "@playwright/test";

const targetFile = process.env.E2E_HERMES_UNMATCHED_FILE;
const verifyOnly = process.env.E2E_VERIFY_EXISTING_IMPORT === "1";

test("未命中区域经 Hermes 识别后仍可正式入库并进入治理队列", async ({ page }) => {
  test.setTimeout(720_000);
  test.skip(!targetFile, "需要显式指定 E2E_HERMES_UNMATCHED_FILE");

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

  await page.getByText(targetFile!, { exact: true }).first().click();
  const drawer = page.getByRole("complementary", { name: "文件处理详情" });
  await expect(drawer).toBeVisible();

  if (!verifyOnly) {
    const reimportResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/reimport")
        && response.request().method() === "POST",
    );
    await drawer.getByRole("button", { name: "重新入库" }).click();
    expect((await reimportResponse).ok()).toBeTruthy();

    await expect.poll(
      async () => {
        const response = await page.request.get("/api/files");
        expect(response.ok()).toBeTruthy();
        const files = await response.json() as Array<{
          id: string;
          batch_id: string;
          original_name: string;
          status: string;
          formal_import_status: string;
          record_count: number;
        }>;
        const item = files.find((file) => file.original_name === targetFile);
        if (
          item
          && item.status === "imported"
          && ["imported", "partial"].includes(item.formal_import_status)
          && item.record_count > 0
        ) {
          return item;
        }
        return null;
      },
      { timeout: 690_000, intervals: [1_000, 2_000, 5_000] },
    ).not.toBeNull();
  }

  await drawer.getByRole("button", { name: "关闭文件详情" }).click();
  const refreshedFiles = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/files")
      && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "刷新" }).click();
  expect((await refreshedFiles).ok()).toBeTruthy();
  await page.getByText(targetFile!, { exact: true }).first().click();
  await expect(drawer.getByText("条 JSONB 正式记录")).toBeVisible();
  await expect(drawer.getByText(/前往管理端治理数据/)).toBeVisible();

  const filesResponse = await page.request.get("/api/files");
  const files = await filesResponse.json() as Array<{
    original_name: string;
    record_count: number;
  }>;
  const persisted = files.find((file) => file.original_name === targetFile);
  expect(persisted?.record_count).toBeGreaterThan(0);

  if (process.env.E2E_HERMES_UNMATCHED_SCREENSHOT_PATH) {
    await page.screenshot({
      path: process.env.E2E_HERMES_UNMATCHED_SCREENSHOT_PATH,
      fullPage: true,
    });
  }
  expect(browserErrors).toEqual([]);
});

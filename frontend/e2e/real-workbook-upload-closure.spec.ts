import { expect, test } from "@playwright/test";

const workbookPath = process.env.E2E_REAL_WORKBOOK_PATH;
const workbookName = workbookPath?.split("/").at(-1);
const villageName = process.env.E2E_REAL_WORKBOOK_VILLAGE ?? "龙塘村";

test("真实多 Sheet 工作簿完成上传、解析与正式入库", async ({ page }) => {
  test.setTimeout(900_000);
  test.skip(!workbookPath || !workbookName, "需要显式指定 E2E_REAL_WORKBOOK_PATH");

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

  const filesBeforeResponse = await page.request.get("/api/files");
  expect(filesBeforeResponse.ok()).toBeTruthy();
  const filesBefore = await filesBeforeResponse.json() as Array<{
    original_name: string;
    administrative_unit_name: string;
    status: string;
  }>;
  const existing = filesBefore.find(
    (file) =>
      file.original_name === workbookName
      && file.administrative_unit_name === villageName,
  );
  const activeStatuses = new Set([
    "pending",
    "profiling",
    "matching",
    "recognizing",
    "materializing",
  ]);
  if (existing && !activeStatuses.has(existing.status)) {
    await page.getByText(workbookName!, { exact: true }).first().click();
    const detail = page.getByRole("complementary", { name: "文件处理详情" });
    const reimportResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/reimport")
        && response.request().method() === "POST",
    );
    await detail.getByRole("button", { name: "重新入库" }).click();
    expect((await reimportResponse).ok()).toBeTruthy();
    await detail.getByRole("button", { name: "关闭文件详情" }).click();
  } else if (!existing) {
    await page.getByRole("button", { name: "＋ 导入文件" }).click();
    const drawer = page.getByRole("complementary", { name: "导入结构化文件" });
    await expect(drawer).toBeVisible();
    await drawer.getByLabel("所属村").selectOption({ label: villageName });
    await drawer.getByLabel("批次名称").fill(
      process.env.E2E_REAL_WORKBOOK_BATCH ?? "龙塘村真实多Sheet闭环",
    );
    await drawer.getByLabel("选择结构化文件").setInputFiles(workbookPath!);

    const uploadResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/api/batches/")
        && response.url().endsWith("/files")
        && response.request().method() === "POST",
    );
    await drawer.getByRole("button", { name: "开始自动入库" }).click();
    expect((await uploadResponse).ok()).toBeTruthy();
  }

  await expect.poll(
    async () => {
      const response = await page.request.get("/api/files");
      expect(response.ok()).toBeTruthy();
      const files = await response.json() as Array<{
        id: string;
        batch_id: string;
        original_name: string;
        administrative_unit_name: string;
        status: string;
        formal_import_status: string;
        record_count: number;
        hermes_call_count: number;
        error_message: string | null;
      }>;
      const item = files.find(
        (file) =>
          file.original_name === workbookName
          && file.administrative_unit_name === villageName,
      );
      return item?.status ?? "missing";
    },
    { timeout: 840_000, intervals: [1_000, 2_000, 5_000, 10_000] },
  ).toMatch(/^(imported|failed|needs_review)$/);

  const filesAfterResponse = await page.request.get("/api/files");
  expect(filesAfterResponse.ok()).toBeTruthy();
  const filesAfter = await filesAfterResponse.json() as Array<{
    original_name: string;
    administrative_unit_name: string;
    status: string;
    formal_import_status: string;
    record_count: number;
  }>;
  const terminalItem = filesAfter.find(
    (file) =>
      file.original_name === workbookName
      && file.administrative_unit_name === villageName,
  );
  expect(terminalItem?.status).toBe("imported");
  expect(terminalItem?.formal_import_status).toMatch(/^(imported|partial)$/);
  expect(terminalItem?.record_count).toBeGreaterThan(0);

  await page.getByRole("button", { name: "刷新" }).click();
  await page.getByText(workbookName!, { exact: true }).first().click();
  const detail = page.getByRole("complementary", { name: "文件处理详情" });
  await expect(detail).toBeVisible();
  await expect(detail.getByText("条 JSONB 正式记录")).toBeVisible();

  if (process.env.E2E_REAL_WORKBOOK_SCREENSHOT_PATH) {
    await page.screenshot({
      path: process.env.E2E_REAL_WORKBOOK_SCREENSHOT_PATH,
      fullPage: true,
    });
  }
  expect(browserErrors).toEqual([]);
});

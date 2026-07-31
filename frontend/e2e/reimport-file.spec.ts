import { expect, test } from "@playwright/test";

const targetFile = process.env.E2E_REIMPORT_FILE;

test("按文件一键重新入库且恢复原记录数", async ({ page }) => {
  test.setTimeout(3_600_000);
  test.skip(!targetFile, "需要显式指定 E2E_REIMPORT_FILE");
  const expectedRecords = Number(process.env.E2E_REIMPORT_RECORDS ?? "0");
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

  const search = page.getByLabel("搜索文件、批次或村");
  await search.fill(targetFile!);
  await expect(page.getByText(targetFile!, { exact: true })).toBeVisible();
  await page.getByText(targetFile!, { exact: true }).click();
  const drawer = page.getByRole("complementary", { name: "文件处理详情" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(`${expectedRecords}`, { exact: true })).toBeVisible();

  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/reimport")
      && response.request().method() === "POST",
  );
  await drawer.getByRole("button", { name: "重新入库" }).click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  await expect(drawer.getByText("0", { exact: true })).toBeVisible();

  await expect.poll(
    async () => {
      const filesResponse = await page.request.get(
        `/api/files?limit=100&offset=0&search=${encodeURIComponent(targetFile!)}`,
      );
      const files = await filesResponse.json() as {
        items: Array<{
          original_name: string;
          status: string;
          formal_import_status: string;
          record_count: number;
          partial_record_count: number;
        }>;
      };
      const item = files.items.find((file) => file.original_name === targetFile);
      return [
        item?.status,
        item?.formal_import_status,
        item?.record_count,
        item?.partial_record_count,
      ].join(":");
    },
    { timeout: 3_000_000, intervals: [2_000, 5_000, 10_000, 20_000] },
  ).toBe(`imported:imported:${expectedRecords}:0`);

  expect(browserErrors).toEqual([]);
});

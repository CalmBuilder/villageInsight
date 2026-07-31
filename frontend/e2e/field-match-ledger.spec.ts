import { expect, test } from "@playwright/test";

test("文件详情展示字段复用与待识别判断", async ({ page }) => {
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

  const filesResponse = await page.request.get("/api/files");
  expect(filesResponse.ok()).toBeTruthy();
  const files = await filesResponse.json() as Array<{
    id: string;
    batch_id: string;
    original_name: string;
    status: string;
  }>;
  let target: typeof files[number] | undefined;
  let matches: Array<{ requires_hermes: boolean }> = [];
  for (const file of files) {
    if (!["ready", "needs_review", "materializing", "imported"].includes(file.status)) {
      continue;
    }
    const response = await page.request.get(
      `/api/batches/${file.batch_id}/items/${file.id}/field-matches`,
    );
    if (!response.ok()) continue;
    const candidate = await response.json() as Array<{ requires_hermes: boolean }>;
    if (candidate.length) {
      target = file;
      matches = candidate;
      break;
    }
  }
  expect(target).toBeDefined();

  await page.getByText(target!.original_name, { exact: true }).first().click();
  const drawer = page.getByRole("complementary", { name: "文件处理详情" });
  await expect(drawer).toBeVisible();
  const reused = matches.filter((field) => !field.requires_hermes).length;
  await expect(
    drawer.locator("dl").getByText(`${reused}/${matches.length}`, { exact: true }),
  ).toBeVisible();
  await drawer.getByText("查看字段判断", { exact: false }).click();
  await expect(drawer.locator(".field-match-ledger p")).toHaveCount(matches.length);

  expect(browserErrors).toEqual([]);
});

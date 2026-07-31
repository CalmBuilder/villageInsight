import { expect, test } from "@playwright/test";

test("管理端展示完整四层模板目录", async ({ page }) => {
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
  await page.getByRole("link", { name: /字段与模板/ }).click();

  await expect(page).toHaveURL(/\/admin\/catalog$/);
  await expect(page.getByRole("heading", { name: "Excel 入库模板" })).toBeVisible();
  await expect(page.getByRole("region", { name: "入库字段目录" })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Excel 入库模板分类" });
  await navigation.getByRole("link", { name: /表头模板/ }).click();
  await expect(page.getByRole("region", { name: "表头模板目录" })).toBeVisible();
  await navigation.getByRole("link", { name: /Sheet 模板/ }).click();
  await expect(page.getByRole("region", { name: "Sheet 模板目录" })).toBeVisible();
  await navigation.getByRole("link", { name: /文件模板/ }).click();
  await expect(page.getByRole("region", { name: "文件模板目录" })).toBeVisible();
  const regionTemplatesResponse = await page.request.get(
    "/api/region-templates",
  );
  expect(regionTemplatesResponse.ok()).toBeTruthy();
  const regionTemplates = await regionTemplatesResponse.json() as unknown[];
  expect(regionTemplates.length).toBeGreaterThan(0);
  const sheetCompositionsResponse = await page.request.get(
    "/api/sheet-compositions",
  );
  expect(sheetCompositionsResponse.ok()).toBeTruthy();
  const sheetCompositions =
    await sheetCompositionsResponse.json() as unknown[];
  const workbookRoutesResponse = await page.request.get(
    "/api/workbook-routes",
  );
  expect(workbookRoutesResponse.ok()).toBeTruthy();
  const workbookRoutes = await workbookRoutesResponse.json() as unknown[];
  const seedResponse = await page.request.get(
    "/api/template-seeds?status=pending&limit=100",
  );
  expect(seedResponse.ok()).toBeTruthy();
  const seeds = await seedResponse.json() as Array<{
    model_name: string | null;
    prompt_version: string | null;
    proposal: { contract_version?: string };
  }>;
  expect(
    seeds.some(
      (seed) =>
        seed.model_name === "codex"
        && seed.prompt_version === "codex-four-layer-bootstrap/v3"
        && seed.proposal.contract_version === "four-layer-template-seed/v3",
    ),
  ).toBeTruthy();
  await navigation.getByRole("link", { name: /表头模板/ }).click();
  await expect(page.locator(".catalog-workbench__actions strong")).toHaveText(
    String(regionTemplates.length),
  );
  await navigation.getByRole("link", { name: /Sheet 模板/ }).click();
  await expect(page.locator(".catalog-workbench__actions strong")).toHaveText(
    String(sheetCompositions.length),
  );
  await navigation.getByRole("link", { name: /文件模板/ }).click();
  await expect(page.locator(".catalog-workbench__actions strong")).toHaveText(
    [String(workbookRoutes.length), "141"],
  );
  await page.getByRole("searchbox", { name: "搜索当前目录" }).fill(
    "木渣黑社区户籍人口信息2020.9",
  );
  await expect(page.getByText("1 项结果", { exact: true })).toBeVisible();
  await page.locator(".catalog-row-button").first().click();
  await expect(
    page.getByText(
      "木渣黑社区户籍人口信息2020.9（木渣黑）(1)(1)(1).xlsx",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByText("Sheet1", { exact: true })).toBeVisible();
  await expect(page.getByText("五组", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../test-results/four-layer-workbook-route-evidence.png",
    fullPage: true,
  });
  await navigation.getByRole("link", { name: /入库字段/ }).click();
  await expect(page.locator(".catalog-table tbody tr").first()).toBeVisible();
  await navigation.getByRole("link", { name: /表头模板/ }).click();
  await page.getByRole("searchbox", { name: "搜索当前目录" }).fill(
    "杜市镇先进社区道路硬化情况",
  );
  await expect(page.getByText("1 项结果", { exact: true })).toBeVisible();
  await page.locator(".catalog-row-button").first().click();
  await expect(
    page.getByText("杜市镇先进社区道路硬化情况.xlsx", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Sheet1", { exact: true })).toBeVisible();
  await expect(page.getByText("A1:T7", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "每一列如何入库" })).toBeVisible();
  const firstColumn = page.locator(".catalog-column-card").first();
  await expect(firstColumn.getByText("A", { exact: true })).toBeVisible();
  await expect(firstColumn.getByText("序号", { exact: true }).first()).toBeVisible();
  await expect(firstColumn.getByText(/486/)).toBeVisible();
  await page.screenshot({
    path: "../test-results/four-layer-catalog-evidence.png",
    fullPage: true,
  });
  await navigation.getByRole("link", { name: /历史记录/ }).click();
  await expect(page.getByRole("region", { name: "历史记录目录" })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

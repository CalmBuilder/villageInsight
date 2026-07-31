import { expect, test } from "@playwright/test";

async function loginAsGovernor(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByLabel("用户名").fill(
    process.env.E2E_GOVERNOR_USERNAME ?? "admin",
  );
  await page.getByLabel("密码").fill(
    process.env.E2E_GOVERNOR_PASSWORD ?? process.env.E2E_PASSWORD ?? "admin",
  );
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page).toHaveURL(/\/admin\/reviews$/);
}

test("管理端使用侧栏、抽屉和分类页签组织高频任务", async ({ page }) => {
  await loginAsGovernor(page);

  await page.goto("/admin/access");
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "用户与租户控制台" })).toBeVisible();
  const accessNavigation = page.getByRole("navigation", { name: "用户与租户目录" });
  await expect(accessNavigation).toBeVisible();
  await accessNavigation.getByRole("link", { name: /用户/ }).click();
  await expect(page).toHaveURL(/type=users/);
  const userSearch = page.getByRole("searchbox", { name: "搜索当前目录" });
  await userSearch.fill("admin");
  await expect(page).toHaveURL(/q=admin/);
  await page.locator(".access-row-button").first().click();
  await expect(page).toHaveURL(/selected=/);
  await expect(page.getByRole("complementary", { name: "权限边界详情" })).toBeVisible();
  await accessNavigation.getByRole("link", { name: /租户/ }).click();
  await page.getByRole("button", { name: "新增业务租户" }).click();
  await expect(page.getByRole("complementary", { name: "用户与租户操作" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "新增业务租户" })).toBeVisible();
  await page.getByRole("button", { name: "关闭" }).click();
  await expect(page.getByRole("button", { name: /删除/ })).toHaveCount(0);
  await expect(page.locator(".access-kind-nav").getByText("当前操作者")).toHaveCount(0);

  await page.getByRole("link", { name: "进入用户端 · 只读" }).click();
  await expect(page).toHaveURL(/\/batches$/);
  await expect(page.getByRole("heading", { name: "业务文件台账" })).toBeVisible();
  await expect(page.getByLabel("当前数据范围")).toContainText("全部业务租户");
  await expect(page.getByLabel("当前数据范围")).toContainText("只读");
  await expect(page.getByText("用户端只读视图")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /导入文件/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /可信问数/ })).toHaveCount(0);
  if (process.env.E2E_READONLY_LEDGER_SCREENSHOT_PATH) {
    await page.screenshot({
      path: process.env.E2E_READONLY_LEDGER_SCREENSHOT_PATH,
      fullPage: true,
    });
  }
  await page.getByRole("link", { name: /返回管理端/ }).click();
  await expect(page).toHaveURL(/\/admin\/reviews$/);

  await page.goto("/admin/catalog");
  const catalogNavigation = page.getByRole("navigation", { name: "Excel 入库模板分类" });
  await expect(catalogNavigation).toBeVisible({ timeout: 15_000 });
  const catalogSearch = page.getByRole("searchbox", { name: "搜索当前目录" });
  await catalogSearch.fill("市级行政区");
  await expect(page).toHaveURL(/q=%E5%B8%82%E7%BA%A7%E8%A1%8C%E6%94%BF%E5%8C%BA/);
  await page.locator(".catalog-row-button").first().click();
  await expect(page).toHaveURL(/selected=/);
  await page.getByRole("button", { name: "复用变体" }).click();
  await expect(page).toHaveURL(/view=variants/);
  await page.getByRole("button", { name: "新建业务字段" }).click();
  await expect(page.getByRole("complementary", { name: "新建业务字段" })).toBeVisible();
  await page.getByRole("complementary", { name: "新建业务字段" })
    .getByRole("button", { name: "关闭" })
    .click();
  await catalogNavigation.getByRole("link", { name: /表头模板/ }).click();
  await expect(page).toHaveURL(/type=regions/);
  await expect(page.getByRole("region", { name: "表头模板目录" })).toBeVisible();

  await page.goto("/admin/records");
  const recordTree = page.getByRole("tree", { name: "正式入库文件目录" });
  await expect(recordTree).toBeVisible();
  const treeScrollHeight = await recordTree.evaluate((element) => element.scrollHeight);
  const treeClientHeight = await recordTree.evaluate((element) => element.clientHeight);
  if (treeScrollHeight > treeClientHeight) {
    await recordTree.hover();
    await page.mouse.wheel(0, 500);
    await expect.poll(
      () => recordTree.evaluate((element) => element.scrollTop),
    ).toBeGreaterThan(0);
  }
  const firstRecord = page.locator(".record-tree__children > button").first();
  if (await firstRecord.count()) {
    await firstRecord.click();
    await expect(page.getByRole("navigation", { name: "记录详情" })).toBeVisible();
    await page.getByRole("button", { name: "来源证据" }).click();
    await expect(page.getByRole("heading", { name: "证据轨" })).toBeVisible();
  }
});

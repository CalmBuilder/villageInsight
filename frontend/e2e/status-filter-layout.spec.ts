import { expect, test } from "@playwright/test";

const filters = [
  ["全部文件", "205", "已接收"],
  ["已正式入库", "205", "可用于问数"],
  ["部分语义", "56", "原值已保留"],
  ["自动处理中", "0", "后台执行"],
  ["AI 辅助", "9", "仅在必要时"],
  ["待治理", "0", "不阻断入库"],
  ["处理失败", "0", "可以重试"],
] as const;

async function renderStatusFilters(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.locator("#root").evaluate((root, entries) => {
    root.innerHTML = `
      <main style="padding-top: 40px">
        <div class="status-filters" aria-label="文件状态筛选">
          ${entries.map(([label, count, hint], index) => `
            <button type="button" aria-pressed="${index === 0}">
              <span>${label}</span><strong>${count}</strong><small>${hint}</small>
            </button>
          `).join("")}
        </div>
      </main>
    `;
  }, filters);
}

test("文件状态栏在桌面和窄屏都保持单行", async ({ page }) => {
  await page.setViewportSize({ width: 1827, height: 500 });
  await renderStatusFilters(page);

  const bar = page.getByLabel("文件状态筛选");
  const desktopBoxes = await bar.getByRole("button").evaluateAll((buttons) =>
    buttons.map((button) => button.getBoundingClientRect().toJSON()),
  );
  expect(new Set(desktopBoxes.map((box) => Math.round(box.y))).size).toBe(1);
  expect(Math.max(...desktopBoxes.map((box) => box.bottom)) - Math.min(...desktopBoxes.map((box) => box.top))).toBeLessThan(70);

  if (process.env.E2E_STATUS_FILTER_SCREENSHOT_PATH) {
    await page.screenshot({
      path: process.env.E2E_STATUS_FILTER_SCREENSHOT_PATH,
      fullPage: true,
    });
  }

  await page.setViewportSize({ width: 760, height: 500 });
  const mobileBoxes = await bar.getByRole("button").evaluateAll((buttons) =>
    buttons.map((button) => button.getBoundingClientRect().toJSON()),
  );
  expect(new Set(mobileBoxes.map((box) => Math.round(box.y))).size).toBe(1);
  await expect.poll(() => bar.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
});

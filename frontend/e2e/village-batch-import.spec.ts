import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

type ManifestRow = {
  relative_path: string;
  village: string;
  username: string;
  classification: string;
};

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const manifest = JSON.parse(
  fs.readFileSync(
    path.join(
      projectRoot,
      "docs/batch-preparation/all-villages-import-manifest.json",
    ),
    "utf8",
  ),
) as { files: ManifestRow[] };
const village = process.env.E2E_IMPORT_VILLAGE;
const reimportPartial = process.env.E2E_REIMPORT_PARTIAL === "1";
const reimportFile = process.env.E2E_REIMPORT_FILE;
const rows = manifest.files.filter(
  (row) => row.village === village && row.classification === "ready",
);

test("村级操作员从前端批量上传本村预检文件并等待处理完成", async ({ page }) => {
  test.setTimeout(3_600_000);
  test.skip(!village, "需要设置 E2E_IMPORT_VILLAGE");
  expect(rows.length).toBeGreaterThan(0);
  const username = rows[0].username;
  expect(rows.every((row) => row.username === username)).toBe(true);
  const filePaths = rows.map((row) =>
    path.join(projectRoot, "docs/datafiles/所有村", row.relative_path)
  );
  expect(filePaths.every((filePath) => fs.existsSync(filePath))).toBe(true);

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
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("demo");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("heading", { name: "文件入库" })).toBeVisible();
  await expect(page.locator(".workspace-scope")).toContainText(village!);

  const beforeResponse = await page.request.get("/api/files?limit=100&offset=0");
  expect(beforeResponse.ok()).toBeTruthy();
  const before = await beforeResponse.json() as {
    total: number;
    items: Array<{
      original_name: string;
      status: string;
      formal_import_status: string;
      error_code: string | null;
      partial_record_count: number;
    }>;
  };
  if (before.total === 0) {
    await page.getByRole("button", { name: "＋ 导入文件" }).click();
    const drawer = page.getByRole("complementary", { name: "导入结构化文件" });
    await drawer.getByLabel("批次名称").fill(`${village}全量真实文件入库`);
    await drawer.getByLabel("选择结构化文件").setInputFiles(filePaths);
    await expect(drawer.getByText(`已选择 ${rows.length} 个文件`)).toBeVisible();
    await drawer.getByRole("button", { name: "开始自动入库" }).click();
    await expect(drawer).toBeHidden({ timeout: 900_000 });
  } else {
    expect(before.total).toBe(rows.length);
    const retryable = before.items.filter(
      (candidate) =>
        candidate.status === "failed"
        || candidate.original_name === reimportFile
        || (
          reimportPartial
          && (
            candidate.formal_import_status === "partial"
            || candidate.partial_record_count > 0
          )
        )
        || (
          candidate.status === "needs_review"
          && (
            candidate.error_code === "AUTO_IMPORT_PLAN_BLOCKED"
            || candidate.error_code === "HERMES_RECOGNITION_FAILED"
          )
        ),
    );
    for (const item of retryable) {
      const previousDetail = page.getByRole(
        "complementary",
        { name: "文件处理详情" },
      );
      if (await previousDetail.isVisible()) {
        await previousDetail.getByRole(
          "button",
          { name: "关闭文件详情" },
        ).click();
        await expect(previousDetail).toBeHidden();
      }
      const search = page.getByLabel("搜索文件、批次或村");
      await search.fill(item.original_name);
      await expect(
        page.getByText(item.original_name, { exact: true }).first(),
      ).toBeVisible();
      await page.getByText(item.original_name, { exact: true }).first().click();
      const detail = page.getByRole("complementary", { name: "文件处理详情" });
      const response = page.waitForResponse(
        (candidate) =>
          candidate.request().method() === "POST"
          && candidate.url().endsWith("/reimport"),
      );
      await detail.getByRole("button", { name: "重新入库" }).click();
      expect((await response).ok()).toBeTruthy();
      await detail.getByRole("button", { name: "关闭文件详情" }).click();
      await expect(detail).toBeHidden();
      await search.fill("");
    }
  }

  await expect.poll(
    async () => {
      const response = await page.request.get("/api/files?limit=100&offset=0");
      expect(response.ok()).toBeTruthy();
      const result = await response.json() as { total: number };
      return result.total;
    },
    { timeout: 60_000, intervals: [1_000, 2_000, 5_000] },
  ).toBe(rows.length);

  await expect.poll(
    async () => {
      const response = await page.request.get("/api/files?limit=100&offset=0");
      expect(response.ok()).toBeTruthy();
      const result = await response.json() as {
        items: Array<{
          status: string;
          formal_import_status: string;
          partial_record_count: number;
          created_by_display_name: string;
        }>;
      };
      const active = new Set([
        "pending",
        "profiling",
        "matching",
        "recognizing",
        "ready",
        "materializing",
      ]);
      return {
        active: result.items.filter((item) => active.has(item.status)).length,
        invalid: result.items.filter(
          (item) =>
            item.status !== "imported"
            || item.formal_import_status !== "imported"
            || item.partial_record_count > 0,
        ).length,
        total: result.items.length,
        correctActor: result.items.every(
          (item) => item.created_by_display_name === username,
        ),
      };
    },
    { timeout: 3_000_000, intervals: [2_000, 5_000, 10_000, 20_000] },
  ).toEqual({
    active: 0,
    invalid: 0,
    total: rows.length,
    correctActor: true,
  });

  expect(browserErrors).toEqual([]);
});

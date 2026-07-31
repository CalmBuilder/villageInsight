import { expect, test } from "@playwright/test";

const accounts = [
  ["七里坝", "七里坝"],
  ["先进社区", "先进社区"],
  ["官庄村", "官庄村"],
  ["新场村", "新场村"],
  ["木渣黑社区", "木渣黑社区"],
  ["法乐村", "法乐村"],
  ["燕云村", "燕云村"],
  ["红星村", "红星村"],
  ["群慧村", "群慧村"],
  ["胜丰村", "胜丰村"],
  ["董地村", "董地村"],
  ["龙塘村", "龙塘村"],
] as const;

test("所有村级账号只能进入自己的文件入库范围", async ({ page }) => {
  test.setTimeout(120_000);

  for (const [username, village] of accounts) {
    await page.context().clearCookies();
    await page.goto("/");
    await page.getByLabel("用户名").fill(username);
    await page.getByLabel("密码").fill("demo");
    await page.getByRole("button", { name: "进入工作台" }).click();

    await expect(page.getByRole("heading", { name: "文件入库" })).toBeVisible();
    await expect(page.locator(".workspace-scope")).toContainText(village);
    const currentUserResponse = await page.request.get("/api/auth/me");
    expect(currentUserResponse.ok()).toBeTruthy();
    const currentUser = await currentUserResponse.json() as {
      username: string;
      role: string;
      scope_unit_name: string | null;
    };
    expect(currentUser).toMatchObject({
      username,
      role: "village_operator",
      scope_unit_name: village,
    });
  }
});

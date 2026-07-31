import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../data/playwright-results",
  reporter: [["line"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:9137",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});

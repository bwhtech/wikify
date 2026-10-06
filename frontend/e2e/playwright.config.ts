import { defineConfig, devices } from "@playwright/test";

process.loadEnvFile("e2e/.env.e2e");

export default defineConfig({
	testDir: "./specs",
	outputDir: "./test-results",
	workers: 1,
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	reporter: [["list"], ["html", { outputFolder: "./playwright-report", open: "never" }]],
	use: {
		baseURL: process.env.E2E_BASE_URL,
		viewport: { width: 1440, height: 1000 },
		trace: "retain-on-failure",
		screenshot: "only-on-failure",
	},
	projects: [
		{
			name: "setup",
			testDir: "./setup",
			testMatch: /.*\.setup\.ts/,
			teardown: "cleanup",
		},
		{
			name: "cleanup",
			testDir: "./setup",
			testMatch: /.*\.teardown\.ts/,
		},
		{
			name: "chromium",
			use: {
				...devices["Desktop Chrome"],
				viewport: { width: 1440, height: 1000 },
				storageState: "e2e/.state/user.json",
			},
			dependencies: ["setup"],
		},
	],
});

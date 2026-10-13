import type { Page } from "@playwright/test";
import { expect, test } from "../helpers/test";

const SINGLE = "Wikify Settings";

async function openModels(page: Page): Promise<boolean> {
	await page.goto("/wikify/settings/models");
	await expect(page.getByText("OpenRouter model ids per pipeline step.")).toBeVisible();
	return page.evaluate(() => !!(window as any).developer_mode);
}

test.describe("PR #57 local models toggle", () => {
	let original = 0;

	test.beforeEach(async ({ api }) => {
		original = Number(await api.getValue(SINGLE, SINGLE, "use_local_models")) || 0;
	});

	test.afterEach(async ({ api }) => {
		await api.setValue(SINGLE, SINGLE, { use_local_models: original });
	});

	test("W57-1 the Local models switch saves use_local_models in developer mode @functional @settings", async ({
		page,
		api,
	}) => {
		const developerMode = await openModels(page);
		test.skip(!developerMode, "site is not in developer mode");

		await api.setValue(SINGLE, SINGLE, { use_local_models: 0 });
		await page.reload();
		await expect(page.getByText("Local models", { exact: true })).toBeVisible();

		const toggle = page.getByRole("switch").first();
		await toggle.click();
		await page.getByRole("button", { name: "Save" }).click();
		await expect(page.getByText("Settings saved")).toBeVisible();
		expect(Number(await api.getValue(SINGLE, SINGLE, "use_local_models"))).toBe(1);
	});

	test("W57-2 the Local models switch is hidden outside developer mode @negative @settings", async ({
		page,
	}) => {
		const developerMode = await openModels(page);
		test.skip(developerMode, "site is in developer mode");

		await expect(page.getByText("Agent model")).toBeVisible();
		await expect(page.getByText("Local models", { exact: true })).toHaveCount(0);
	});
});

test.describe("PR #60 settings dialog in the url", () => {
	test("W60-1 a settings link opens on its tab, tabs update the url and Escape returns to the page @functional @settings", async ({
		page,
	}) => {
		await page.goto("/wikify/settings/models");
		await expect(page.getByText("OpenRouter model ids per pipeline step.")).toBeVisible();

		await page.getByRole("tab", { name: "Scoring" }).click();
		await expect(page).toHaveURL(/\/wikify\/settings\/scoring$/);

		await page.keyboard.press("Escape");
		await expect(page).not.toHaveURL(/\/settings\//);
		await expect(page.getByRole("tab", { name: "Scoring" })).toHaveCount(0);
	});

	test("W60-2 an unknown settings tab falls back to the first tab @negative @settings", async ({
		page,
	}) => {
		await page.goto("/wikify/settings/nonsense");
		await expect(page).toHaveURL(/\/wikify\/settings\/openrouter$/);
		await expect(page.getByText("OpenRouter model ids per pipeline step.")).toBeHidden();
	});
});

import type { Api } from "../helpers/api";
import { expect, test } from "../helpers/test";

function summary(api: Api, args: Record<string, any> = {}) {
	return api.call("wikify.api.dashboard.summary", args);
}

test.describe("PR #127 dashboard drill-down", () => {
	test(
		"W127-1 drill from project to category to subcategory to a section dialog",
		{ tag: ["@functional", "@parse"] },
		async ({ page, api, fixture }) => {
			const byProject = await summary(api, { project: fixture.project });
			const category = byProject.categories[0];
			expect(category, "fixture project has categorised sections").toBeTruthy();
			const inCategory = await summary(api, {
				project: fixture.project,
				section_type: category.type_name,
			});
			const subcategory = inCategory.subcategories[0];
			const inSub = await summary(api, {
				project: fixture.project,
				section_type: category.type_name,
				subcategory: subcategory.title,
			});
			const section = inSub.sections[0];
			const detail = await api.call("wikify.api.dashboard.section_detail", {
				name: section.name,
			});

			await page.goto("/wikify/dashboard");
			await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
			await expect(page.getByText("Documents", { exact: true })).toBeVisible();

			await page.getByRole("button", { name: fixture.projectName }).click();
			await page.getByRole("button", { name: category.label }).first().click();
			await page.getByRole("button", { name: subcategory.title }).first().click();
			await expect(page.getByRole("button", { name: "All projects" }).first()).toBeVisible();

			await page.getByRole("button", { name: section.title }).first().click();
			const dialog = page.getByRole("dialog");
			await expect(dialog.getByRole("heading", { name: detail.title })).toBeVisible();
			await expect(dialog.getByText(detail.doc_title).first()).toBeVisible();
		},
	);

	test(
		"W127-2 section_detail rejects a section that does not exist",
		{ tag: ["@negative", "@parse"] },
		async ({ api }) => {
			const response = await api.context.post("/api/method/wikify.api.dashboard.section_detail", {
				data: { name: "does-not-exist-w127" },
			});
			expect(response.status()).toBe(404);
		},
	);
});

import { expect, type Page } from "@playwright/test";
import type { Row } from "./api";

export function sectionRow(page: Page, name: string) {
	return page.locator(`[data-section-row="${name}"]`);
}

export async function openTree(
	page: Page,
	importName: string,
	sectionName?: string,
): Promise<void> {
	await page.goto(`/wikify/import/${importName}/tree`);
	await expect(
		sectionName ? sectionRow(page, sectionName) : page.locator("[data-section-row]").first(),
	).toBeVisible();
}

export async function chooseRowAction(page: Page, name: string, action: string): Promise<void> {
	const row = sectionRow(page, name);
	await row.scrollIntoViewIfNeeded();
	await row.hover();
	await row.locator('[aria-haspopup="menu"]').click();
	await page.getByRole("menuitem", { name: action }).click();
}

export function waitForSectionCall(page: Page, method: string) {
	return page.waitForResponse(
		(response) =>
			response.url().includes(`wikify.api.sections.${method}`) &&
			response.request().method() === "POST",
	);
}

function treeRow(page: Page, name: string) {
	return page.locator('[data-slot="row"]', { has: sectionRow(page, name) });
}

// Mouse drags drop about one HTML5 drop in two; dispatching the drag events directly always registers.
export async function dragRow(
	page: Page,
	source: string,
	target: string,
	edge: "before" | "after",
): Promise<void> {
	await treeRow(page, target).scrollIntoViewIfNeeded();
	await page.evaluate(
		({ source, target, edge }) => {
			const row = (name: string) =>
				document.querySelector(`[data-section-row="${name}"]`)!.closest('[data-slot="row"]')!;
			const from = row(source);
			const to = row(target);
			const rect = to.getBoundingClientRect();
			const dataTransfer = new DataTransfer();
			const fire = (element: Element, type: string) =>
				element.dispatchEvent(
					new DragEvent(type, {
						bubbles: true,
						cancelable: true,
						dataTransfer,
						clientX: rect.left + 80,
						clientY: edge === "before" ? rect.top + 2 : rect.bottom - 2,
					}),
				);
			fire(from, "dragstart");
			fire(to, "dragenter");
			fire(to, "dragover");
			fire(to, "drop");
			fire(from, "dragend");
		},
		{ source, target, edge },
	);
}

export async function rowOrder(page: Page): Promise<string[]> {
	return page
		.locator("[data-section-row]")
		.evaluateAll((rows) => rows.map((row) => row.getAttribute("data-section-row")!));
}

export function childOrder(rows: Row[], parent: string): string[] {
	return rows.filter((row) => row.parent_source_section === parent).map((row) => row.name);
}

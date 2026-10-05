import type { Page } from "@playwright/test";
import { serverMessage, type Api } from "../helpers/api";
import { deleteTestProjects } from "../helpers/cleanup";
import { PREFIX } from "../helpers/env";
import { expect, test } from "../helpers/test";
import {
	childOrder,
	chooseRowAction,
	dragRow,
	openTree,
	rowOrder,
	sectionRow,
	waitForSectionCall,
} from "../helpers/tree";
import {
	createProject,
	FIXTURE_ROOT,
	FIXTURE_SECTIONS,
	findSection,
	outline,
	sectionRows,
	startImport,
	waitForImport,
	type SectionRow,
} from "../helpers/wikify";

async function expectStruck(page: Page, rows: SectionRow[], struck: boolean): Promise<void> {
	for (const row of rows) {
		const title = sectionRow(page, row.name).getByText(row.title, { exact: true });
		if (struck) await expect(title).toHaveCSS("text-decoration-line", "line-through");
		else await expect(title).not.toHaveCSS("text-decoration-line", "line-through");
	}
}

async function restoreTree(
	api: Api,
	sourceDocument: string,
	original: SectionRow[],
): Promise<void> {
	const byName = new Map(original.map((row) => [row.name, row]));
	const current = await sectionRows(api, sourceDocument);
	const extras = current.filter((row) => !byName.has(row.name));
	for (const row of extras) {
		if (!row.parent_source_section || byName.has(row.parent_source_section)) {
			await api.call("wikify.api.sections.delete_section", { name: row.name });
		}
	}
	for (const row of current) {
		const before = byName.get(row.name);
		if (before && before.title !== row.title) {
			await api.call("wikify.api.sections.rename_section", {
				name: row.name,
				title: before.title,
			});
		}
	}
	for (const before of original) {
		const now = (await sectionRows(api, sourceDocument)).find((row) => row.name === before.name);
		if (now && now.include_in_wiki !== before.include_in_wiki) {
			await api.call("wikify.api.sections.toggle_include", {
				name: before.name,
				include: before.include_in_wiki,
			});
		}
	}
	const root = original.find((row) => !row.parent_source_section)!;
	const childOrder = original
		.filter((row) => row.parent_source_section === root.name)
		.map((row) => row.name);
	const currentOrder = (await sectionRows(api, sourceDocument))
		.filter((row) => row.parent_source_section === root.name)
		.map((row) => row.name);
	if (JSON.stringify(currentOrder) !== JSON.stringify(childOrder)) {
		await api.call("wikify.api.sections.reorder_section", {
			name: childOrder[0],
			new_parent: root.name,
			new_index: 0,
			siblings: childOrder,
		});
	}
}

test.describe("tree", () => {
	let original: SectionRow[] = [];

	test.beforeEach(async ({ api, fixture }) => {
		original = await sectionRows(api, fixture.review.sourceDocument);
	});

	test.afterEach(async ({ api, fixture }) => {
		await restoreTree(api, fixture.review.sourceDocument, original);
	});

	test(
		"F-TREE-01 rename persists after reload",
		{ tag: ["@functional", "@tree", "@sanity"] },
		async ({ page, api, fixture }) => {
			const section = await findSection(api, fixture.review.sourceDocument, FIXTURE_SECTIONS[2]);
			const newTitle = `${PREFIX} tree ${Date.now()} renamed`;
			const renameCalls: string[] = [];
			page.on("request", (request) => {
				if (
					request.method() === "POST" &&
					request.url().includes("wikify.api.sections.rename_section")
				) {
					renameCalls.push(request.url());
				}
			});

			await openTree(page, fixture.review.import, section.name);
			await chooseRowAction(page, section.name, "Rename");
			const input = sectionRow(page, section.name).getByRole("textbox");
			await input.click();
			await input.fill(newTitle);
			const renamed = waitForSectionCall(page, "rename_section");
			await input.press("Enter");
			expect((await renamed).ok()).toBe(true);

			await expect(
				sectionRow(page, section.name).getByText(newTitle, { exact: true }),
			).toBeVisible();
			await page.reload();
			await expect(
				sectionRow(page, section.name).getByText(newTitle, { exact: true }),
			).toBeVisible();
			expect(renameCalls).toHaveLength(1);
			expect(await api.getValue("Source Section", section.name, "title")).toBe(newTitle);
		},
	);

	test(
		"F-TREE-02 exclude a parent section strikes it and its children",
		{ tag: ["@functional", "@tree", "@sanity"] },
		async ({ page, api, fixture }) => {
			const rows = await sectionRows(api, fixture.review.sourceDocument);
			const root = rows.find((row) => row.title === FIXTURE_ROOT && !row.parent_source_section)!;
			const subtree = rows.filter(
				(row) => row.name === root.name || row.parent_source_section === root.name,
			);

			await openTree(page, fixture.review.import, root.name);
			const toggled = waitForSectionCall(page, "toggle_include");
			await chooseRowAction(page, root.name, "Exclude from wiki");
			expect((await toggled).ok()).toBe(true);

			await expectStruck(page, subtree, true);
			await page.reload();
			await expectStruck(page, subtree, true);
			const after = await sectionRows(api, fixture.review.sourceDocument);
			expect(
				after
					.filter((row) => subtree.some((s) => s.name === row.name))
					.map((row) => row.include_in_wiki),
			).toEqual(subtree.map(() => 0));
		},
	);

	test(
		"F-TREE-03 include the parent section again",
		{ tag: ["@functional", "@tree"] },
		async ({ page, api, fixture }) => {
			const rows = await sectionRows(api, fixture.review.sourceDocument);
			const root = rows.find((row) => row.title === FIXTURE_ROOT && !row.parent_source_section)!;
			const subtree = rows.filter(
				(row) => row.name === root.name || row.parent_source_section === root.name,
			);
			await api.call("wikify.api.sections.toggle_include", { name: root.name, include: 0 });

			await openTree(page, fixture.review.import, root.name);
			await expectStruck(page, subtree, true);
			const toggled = waitForSectionCall(page, "toggle_include");
			await chooseRowAction(page, root.name, "Include in wiki");
			expect((await toggled).ok()).toBe(true);

			await expectStruck(page, subtree, false);
			const after = await sectionRows(api, fixture.review.sourceDocument);
			expect(after.map((row) => row.include_in_wiki)).toEqual(after.map(() => 1));
		},
	);

	test(
		"F-TREE-04 delete a parent section with nested sections",
		{ tag: ["@functional", "@tree"] },
		async ({ page, api, fixture }) => {
			const sourceDocument = fixture.review.sourceDocument;
			const before = await sectionRows(api, sourceDocument);
			const title = `${PREFIX} tree ${Date.now()} parent`;
			const parent = (
				await api.call("wikify.api.sections.create_section", {
					source_document: sourceDocument,
					title,
					is_group: 1,
				})
			).name;
			const child = (
				await api.call("wikify.api.sections.create_section", {
					source_document: sourceDocument,
					title: `${title} child 1`,
					parent,
					is_group: 1,
				})
			).name;
			await api.call("wikify.api.sections.create_section", {
				source_document: sourceDocument,
				title: `${title} child 2`,
				parent,
			});
			await api.call("wikify.api.sections.create_section", {
				source_document: sourceDocument,
				title: `${title} grandchild`,
				parent: child,
			});
			expect(await sectionRows(api, sourceDocument)).toHaveLength(before.length + 4);

			await openTree(page, fixture.review.import, parent);
			await chooseRowAction(page, parent, "Delete");
			const dialog = page.getByRole("dialog");
			await expect(dialog).toContainText(
				`Delete "${title}" and its 3 nested sections? This can't be undone.`,
			);
			const deleted = waitForSectionCall(page, "delete_section");
			await dialog.getByRole("button", { name: "Delete" }).click();
			expect((await deleted).ok()).toBe(true);

			await page.reload();
			await expect(sectionRow(page, before[0].name)).toBeVisible();
			await expect(page.getByText(title)).toHaveCount(0);
			const after = await sectionRows(api, sourceDocument);
			expect(after.map((row) => row.name)).toEqual(before.map((row) => row.name));
			expect(after.map((row) => row.title)).toEqual(before.map((row) => row.title));
		},
	);

	test(
		"F-TREE-05 drag a section to the first and the last position",
		{ tag: ["@functional", "@tree"] },
		async ({ page, api, fixture }) => {
			const sourceDocument = fixture.review.sourceDocument;
			const root = original.find((row) => !row.parent_source_section)!;
			const children = childOrder(original, root.name);
			const moved = children.at(-1)!;
			const others = children.slice(0, -1);
			let reorderCalls = 0;
			page.on("request", (request) => {
				if (
					request.method() === "POST" &&
					request.url().includes("wikify.api.sections.reorder_section")
				)
					reorderCalls++;
			});

			await openTree(page, fixture.review.import, moved);
			let saved = waitForSectionCall(page, "reorder_section");
			await dragRow(page, moved, children[0], "before");
			expect((await saved).ok()).toBe(true);
			expect(reorderCalls).toBe(1);
			await page.reload();
			await expect(sectionRow(page, moved)).toBeVisible();
			expect(await rowOrder(page)).toEqual([root.name, moved, ...others]);
			let after = await sectionRows(api, sourceDocument);
			expect(childOrder(after, root.name)).toEqual([moved, ...others]);
			expect(after.find((row) => row.name === moved)!.sort_order).toBe(0);

			saved = waitForSectionCall(page, "reorder_section");
			await dragRow(page, moved, others.at(-1)!, "after");
			expect((await saved).ok()).toBe(true);
			expect(reorderCalls).toBe(2);
			await page.reload();
			await expect(sectionRow(page, moved)).toBeVisible();
			expect(await rowOrder(page)).toEqual([root.name, ...others, moved]);
			after = await sectionRows(api, sourceDocument);
			expect(childOrder(after, root.name)).toEqual([...others, moved]);
			expect(after.find((row) => row.name === moved)!.lft).toBe(
				Math.max(...after.map((row) => row.lft)),
			);
		},
	);

	test(
		"F-TREE-06 preview a section in the Wiki tab",
		{ tag: ["@functional", "@tree"] },
		async ({ page, api, fixture }) => {
			const section = await findSection(api, fixture.review.sourceDocument, FIXTURE_SECTIONS[0]);
			const documentTitle = await api.getValue(
				"Source Document",
				fixture.review.sourceDocument,
				"title",
			);
			expect(section.markdown.match(/!\[/g)).toHaveLength(2);

			await openTree(page, fixture.review.import, section.name);
			await sectionRow(page, section.name).getByText(section.title, { exact: true }).click();

			await expect(page.getByRole("radio", { name: "Rendered" })).toBeChecked();
			const crumbs = [fixture.projectName, documentTitle, FIXTURE_ROOT, section.title];
			await expect(page.getByRole("navigation").filter({ hasText: documentTitle })).toHaveText(
				new RegExp(
					`^${crumbs.map((crumb) => crumb.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s*›\\s*")}$`,
				),
			);
			const article = page.getByRole("article");
			await expect(article.getByRole("heading", { level: 1, name: section.title })).toBeVisible();
			await expect(article.locator("p").first()).not.toBeEmpty();
			const images = article.locator("img");
			await expect(images).toHaveCount(2);
			for (const image of await images.all()) {
				await image.scrollIntoViewIfNeeded();
				await expect
					.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth))
					.toBeGreaterThan(0);
			}
		},
	);
});

test.describe("tree after publish", () => {
	const stamp = Date.now();
	const name = `${PREFIX} tree ${stamp}`;
	test.afterAll(async ({ api }) => {
		test.setTimeout(1_500_000);
		await deleteTestProjects(api, name);
	});

	test(
		"F-TREE-07 edits after publish are rejected",
		{ tag: ["@negative", "@tree"] },
		async ({ page, api }) => {
			test.setTimeout(2_700_000);
			const project = await createProject(api, name);
			const importName = await startImport(api, { title: name, project });
			// One worker serves every agent's parses, so this parse can sit in Queued for a long while.
			const { source_document: sourceDocument } = await waitForImport(
				api,
				importName,
				"Review",
				1_800_000,
			);
			const { rows: sections, root, children: childRows } = await outline(api, sourceDocument);
			const children = childRows.map((row) => row.name);
			const renamed = children[0];
			const removed = children[3];
			const excluded = children.at(-1)!;
			await api.call("wikify.api.sections.toggle_include", { name: excluded, include: 0 });
			await api.call("wikify.api.sections.build_graph", { import_name: importName });
			await api.call("wikify.api.imports.generate_wiki", {
				import_name: importName,
				new_space: { space_name: name, route: `test-tree-${stamp}` },
			});
			await waitForImport(api, importName, "Completed");
			const before = await sectionRows(api, sourceDocument);

			let reorderCalls = 0;
			page.on("request", (request) => {
				if (
					request.method() === "POST" &&
					request.url().includes("wikify.api.sections.reorder_section")
				)
					reorderCalls++;
			});
			await openTree(page, importName, root?.name ?? children[0]);
			await expect(
				page.getByText(
					"This wiki has been published — click a page below to edit it in the Wiki app.",
				),
			).toBeVisible();
			await expect(page.getByText("Published", { exact: true })).toBeVisible();
			await sectionRow(page, removed).hover();
			await expect(page.locator('[data-section-row] [aria-haspopup="menu"]')).toHaveCount(0);
			await dragRow(page, children.at(-1)!, children[0], "before");
			await page.waitForTimeout(1_000);
			expect(reorderCalls).toBe(0);

			const edits: [string, Record<string, any>][] = [
				["rename_section", { name: renamed, title: `${name} rename` }],
				["toggle_include", { name: removed, include: 0 }],
				["toggle_include", { name: excluded, include: 1 }],
				["delete_section", { name: removed }],
				["reorder_section", { name: children.at(-1)!, new_parent: null, new_index: 0 }],
			];
			for (const [method, args] of edits) {
				const response = await api.context.post(`/api/method/wikify.api.sections.${method}`, {
					data: args,
				});
				expect(response.status(), method).toBe(417);
				expect(serverMessage(await response.json()), method).toContain(
					"This wiki has already been published — edit pages directly in the Wiki app.",
				);
			}
			expect(await sectionRows(api, sourceDocument)).toEqual(before);
		},
	);
});

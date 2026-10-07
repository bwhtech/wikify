import type { Page } from "@playwright/test";
import type { Api } from "../helpers/api";
import { PREFIX } from "../helpers/env";
import { expect, test } from "../helpers/test";
import { waitFor } from "../helpers/wait";
import {
	cloneImport,
	createProject,
	findFigure,
	outline,
	sectionRows,
	waitForImport,
	type SectionRow,
} from "../helpers/wikify";

type WikiDocument = {
	name: string;
	title: string;
	route: string;
	is_group: number;
	parent_wiki_document: string;
	content: string;
};

// One background worker serves every agent's parses and jobs, so queues can be long.
const QUEUE_TIMEOUT = 1_800_000;

async function wikiDocuments(api: Api, spaceRoute: string): Promise<WikiDocument[]> {
	return api.getList<WikiDocument>("Wiki Document", {
		filters: { route: ["like", `${spaceRoute}/%`] },
		fields: ["name", "title", "route", "is_group", "parent_wiki_document", "content"],
		orderBy: "lft asc",
	});
}

async function openPublishDialog(page: Page, importName: string) {
	await page.goto(`/wikify/import/${importName}/tree`);
	const publish = page.getByRole("button", { name: "Publish", exact: true });
	await expect(publish).toBeEnabled();
	await publish.click();
	const dialog = page.getByRole("dialog");
	await expect(dialog.getByText("Publish wiki")).toBeVisible();
	return dialog;
}

async function expectRoutesOpen(page: Page, documents: WikiDocument[]): Promise<void> {
	for (const document of documents) {
		const response = await page.request.get(`/${document.route}`);
		expect(response.status(), document.route).toBe(200);
	}
}

async function firstLeaf(api: Api, sourceDocument: string): Promise<SectionRow> {
	const { rows, children } = await outline(api, sourceDocument);
	return children.find((child) => !rows.some((row) => row.parent_source_section === child.name))!;
}

function ownParagraph(markdown: string): string {
	const paragraph = markdown
		.split(/\n\s*\n/)
		.map((block) => block.trim())
		.find((block) => block && !block.startsWith("#") && !block.startsWith("!["));
	if (!paragraph)
		throw new Error(`No plain paragraph in ${JSON.stringify(markdown.slice(0, 200))}`);
	return paragraph;
}

test.describe("publish", () => {
	test.describe.configure({ mode: "serial" });

	const stamp = Date.now();
	const name = `${PREFIX} publish ${stamp}`;
	const spaceRoute = `test-publish-${stamp}`;
	let first = { import: "", sourceDocument: "" };
	let second = { import: "", sourceDocument: "" };
	let sections: SectionRow[] = [];
	let root: SectionRow | undefined;
	let excluded: SectionRow;
	let leafTitle = "";
	let otherTitle = "";
	let crop: { url: string; section: SectionRow };
	let space = "";
	let publishedDocuments: WikiDocument[] = [];

	test.beforeAll(async ({ api, fixture }) => {
		test.setTimeout(QUEUE_TIMEOUT + 1_200_000);
		const project = await createProject(api, name);
		first = await cloneImport(api, fixture.source.import, { title: name, project });
		second = await cloneImport(api, fixture.source.import, { title: `${name} 2`, project });

		let rows: SectionRow[];
		let children: SectionRow[];
		({ rows, root, children } = await outline(api, first.sourceDocument));
		// A group's wiki route opens its first child, so the pages checked by title must be leaves.
		const leaves = children.filter(
			(child) => !rows.some((row) => row.parent_source_section === child.name),
		);
		expect(leaves.length, "two top-level sections without children").toBeGreaterThanOrEqual(2);
		const [leaf, other] = leaves;
		excluded = children.findLast((child) => child !== leaf && child !== other)!;
		leafTitle = leaf.title;
		otherTitle = other.title;
		await api.call("wikify.api.sections.toggle_include", { name: excluded.name, include: 0 });

		// Past the first two children and off both leaves' pages: a crop rebuilds every section covering the
		// page from whole pages, so the root's own text would leak into its first child (hiding bug #30), and
		// a shared page would put another section's heading into a leaf's page.
		const onLeafPage = (pageNo: number) =>
			[leaf, other].some((row) => row.page_start <= pageNo && pageNo <= row.page_end);
		let figure = await findFigure(api, first.sourceDocument, {
			fromPage: children[1].page_end + 1,
		});
		while (onLeafPage(figure.pageNo))
			figure = await findFigure(api, first.sourceDocument, { fromPage: figure.pageNo + 1 });
		const result = await api.call("wikify.api.pages.crop_page_figure", {
			source_document: first.sourceDocument,
			page_no: figure.pageNo,
			caption: figure.caption,
			occurrence: 0,
			x0: 0.25,
			y0: 0.25,
			x1: 0.75,
			y1: 0.75,
		});
		const owners = await waitFor(
			async () =>
				(await sectionRows(api, first.sourceDocument)).filter(
					(row) => row.parent_source_section && row.markdown?.includes(result.image_url),
				),
			(rows) => rows.length > 0,
			{
				timeout: 900_000,
				interval: 5_000,
				label: `crop ${result.image_url} propagated to a section`,
			},
		);
		crop = { url: result.image_url, section: owners[0] };
		sections = await sectionRows(api, first.sourceDocument);

		await api.call("wikify.api.sections.build_graph", { import_name: first.import });
		await waitForImport(api, first.import, "Graphed", 120_000);
	});

	test(
		"F-PUB-01 publish dialog previews the pages to be created",
		{
			tag: ["@functional", "@publish"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/44" },
		},
		async ({ page, api }) => {
			const preview = await api.call("wikify.api.imports.preview_wiki", {
				import_name: first.import,
			});
			const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
			const dialog = await openPublishDialog(page, first.import);
			await expect(dialog.getByText("Pages to publish", { exact: true })).toBeVisible();
			await expect(
				dialog.getByText(`${plural(preview.pages, "page")} · ${plural(preview.groups, "group")}`, {
					exact: true,
				}),
			).toBeVisible();
			await expect(
				dialog.getByText(`${preview.excluded} excluded`, { exact: true }),
			).toBeVisible();
			const tree = dialog.getByRole("tree");
			for (const node of preview.tree)
				await expect(tree.getByText(node.title, { exact: true })).toBeVisible();
			await expect(tree.getByText(excluded.title, { exact: true })).toHaveCount(0);
		},
	);

	test(
		"F-PUB-02 publish to a new space through the UI",
		{ tag: ["@functional", "@publish"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_TIMEOUT + 300_000);
			const dialog = await openPublishDialog(page, first.import);
			await dialog.getByRole("radio", { name: "New space" }).click();
			await dialog.getByLabel("Space name").fill(name);
			await expect(dialog.getByLabel("Route")).toHaveValue(spaceRoute);
			await dialog.getByLabel("Route").fill(spaceRoute);
			await dialog.getByRole("button", { name: "Generate wiki" }).click();
			await expect(dialog).toBeHidden();

			const imp = await waitForImport(api, first.import, "Completed", QUEUE_TIMEOUT);
			expect(imp.error).toBeFalsy();
			space = (await api.getList("Wiki Space", { filters: { route: spaceRoute } }))[0]?.name;
			expect(imp.wiki_space).toBe(space);
			expect(await api.getValue("Wiki Space", space, "space_name")).toBe(name);

			publishedDocuments = await wikiDocuments(api, spaceRoute);
			sections = await sectionRows(api, first.sourceDocument);
			const included = sections.filter((row) => row.include_in_wiki);
			expect(publishedDocuments).toHaveLength(included.length + 1);
			expect(new Set(publishedDocuments.map((document) => document.route)).size).toBe(
				publishedDocuments.length,
			);
			const documentNames = publishedDocuments.map((document) => document.name);
			for (const section of included)
				expect(documentNames, section.title).toContain(section.wiki_document);
			await expectRoutesOpen(page, publishedDocuments);

			const leaf = publishedDocuments.find((document) => document.title === leafTitle)!;
			await page.goto(`/${leaf.route}`);
			await expect(
				page.getByRole("main").getByRole("heading", { level: 1, name: leafTitle }),
			).toBeVisible();
		},
	);

	test(
		"F-PUB-02 published wiki shows a parent section's own text",
		{
			tag: ["@functional", "@publish"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/30" },
		},
		async ({ page }) => {
			test.skip(!root, "this parse produced no parent section");
			const parent = sections.find((row) => row.name === root!.name)!;
			const paragraph = ownParagraph(parent.markdown);
			expect(sections.filter((row) => row.markdown?.includes(paragraph))).toEqual([parent]);
			const group = publishedDocuments.find((document) => document.name === parent.wiki_document)!;
			await page.goto(`/${group.route}`);
			test.fail(); // known failure: #30. Remove test.fail() when the issue is closed.
			await expect(page.getByRole("main")).toContainText(paragraph);
		},
	);

	test(
		"F-CROP-05 the published page shows the cropped figure",
		{ tag: ["@functional", "@publish"] },
		async ({ page, api }) => {
			const wikiDocument = await api.getValue(
				"Source Section",
				crop.section.name,
				"wiki_document",
			);
			const route = publishedDocuments.find((document) => document.name === wikiDocument)!.route;
			await page.goto(`/${route}`);
			const image = page.getByRole("main").locator(`img[src$="${crop.url}"]`);
			await image.scrollIntoViewIfNeeded();
			await expect
				.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth), {
					timeout: 30_000,
				})
				.toBeGreaterThan(0);
		},
	);

	test(
		"F-PUB-03 excluded sections are not in the wiki",
		{ tag: ["@functional", "@publish", "@sanity"] },
		async ({ page, api }) => {
			expect(
				await api.getValue("Source Section", excluded.name, ["include_in_wiki", "wiki_document"]),
			).toEqual({
				include_in_wiki: 0,
				wiki_document: null,
			});
			expect(publishedDocuments.map((document) => document.title)).not.toContain(excluded.title);

			const leaf = publishedDocuments.find((document) => document.title === leafTitle)!;
			await page.goto(`/${leaf.route}`);
			const sidebar = page
				.getByRole("navigation")
				.filter({ has: page.getByRole("link", { name: leafTitle }) });
			await expect(sidebar.getByRole("link", { name: otherTitle })).toBeVisible();
			await expect(sidebar.getByText(excluded.title)).toHaveCount(0);
		},
	);

	test(
		"F-PUB-04 publish a second import into the existing space",
		{ tag: ["@functional", "@publish"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_TIMEOUT * 2);
			const { sourceDocument } = second;
			const before = await wikiDocuments(api, spaceRoute);

			await page.goto(`/wikify/import/${second.import}/tree`);
			await page.getByRole("button", { name: "Approve & Build Graph" }).click();
			await waitForImport(api, second.import, "Graphed", 120_000);
			const dialog = await openPublishDialog(page, second.import);
			await dialog.getByRole("radio", { name: "Existing space" }).click();
			await dialog.getByRole("combobox", { name: "Wiki Space" }).click();
			await page.getByRole("option", { name: `${name} (/${spaceRoute})` }).click();
			await expect(page.getByRole("listbox")).toBeHidden();
			await expect(dialog.getByRole("combobox", { name: "Wiki Space" })).toHaveText(
				`${name} (/${spaceRoute})`,
			);
			await dialog.getByRole("button", { name: "Generate wiki" }).click();
			await expect(dialog).toBeHidden();

			const imp = await waitForImport(api, second.import, "Completed", QUEUE_TIMEOUT);
			expect(imp.wiki_space).toBe(space);
			const after = await wikiDocuments(api, spaceRoute);
			const beforeNames = before.map((document) => document.name);
			expect(after.filter((document) => beforeNames.includes(document.name))).toEqual(before);

			const added = after.filter((document) => !beforeNames.includes(document.name));
			const secondSections = await sectionRows(api, sourceDocument);
			expect(added).toHaveLength(secondSections.filter((row) => row.include_in_wiki).length + 1);
			expect(new Set(after.map((document) => document.route)).size).toBe(after.length);
			for (const section of secondSections) {
				expect(
					added.map((document) => document.name),
					section.title,
				).toContain(section.wiki_document);
			}
			const rootGroup = await api.getValue("Wiki Space", space, "root_group");
			const importGroups = after.filter((document) => document.parent_wiki_document === rootGroup);
			expect(importGroups).toHaveLength(2);
			expect(added.filter((document) => document.parent_wiki_document === rootGroup)).toHaveLength(
				1,
			);
			await expectRoutesOpen(page, added);

			const secondLeaf = await firstLeaf(api, sourceDocument);
			const leafOfFirst = await firstLeaf(api, first.sourceDocument);
			for (const leaf of [secondLeaf, leafOfFirst]) {
				const document = after.find((row) => row.name === leaf.wiki_document)!;
				await page.goto(`/${document.route}`);
				const main = page.getByRole("main");
				await expect(main.getByRole("heading", { level: 1, name: leaf.title })).toBeVisible();
				const sidebar = page
					.getByRole("navigation")
					.filter({ has: page.getByRole("link", { name: leaf.title }) });
				for (const group of importGroups) {
					await expect(
						sidebar.getByRole("button", { name: group.title, exact: true }),
					).toBeVisible();
				}
			}
		},
	);

	test(
		"F-PUB-05 stop generation mid-way, then publish again",
		{
			tag: ["@functional", "@publish"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/22" },
		},
		async () => {
			test.skip(
				true,
				"generation makes no AI calls and commits every 50 pages, so even the 98-page fixture is published in seconds, too fast to stop from the UI",
			);
		},
	);
});

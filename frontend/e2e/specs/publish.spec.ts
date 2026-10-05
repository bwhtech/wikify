import type { Page } from "@playwright/test";
import type { Api } from "../helpers/api";
import { deleteTestProjects } from "../helpers/cleanup";
import { PREFIX } from "../helpers/env";
import { expect, test } from "../helpers/test";
import { waitFor } from "../helpers/wait";
import {
	createProject,
	FIXTURE_SECTIONS,
	pageRows,
	sectionRows,
	startImport,
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

const IMAGE_TAG = /!\[([^\]]*)\]\(([^)]*)\)/;
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
	const first = { import: "", sourceDocument: "" };
	const second = { import: "" };
	let sections: SectionRow[] = [];
	let excluded: SectionRow;
	let crop: { url: string; section: SectionRow };
	let space = "";
	let publishedDocuments: WikiDocument[] = [];

	test.beforeAll(async ({ api }) => {
		test.setTimeout(QUEUE_TIMEOUT + 1_200_000);
		const project = await createProject(api, name);
		first.import = await startImport(api, { title: name, project });
		second.import = await startImport(api, { title: `${name} 2`, project });
		first.sourceDocument = (
			await waitForImport(api, first.import, "Review", QUEUE_TIMEOUT)
		).source_document;

		sections = await sectionRows(api, first.sourceDocument);
		excluded = sections.find((row) => row.title === FIXTURE_SECTIONS[7])!;
		await api.call("wikify.api.sections.toggle_include", { name: excluded.name, include: 0 });

		// Page 3, not 1 or 2: a crop rebuilds every section covering the page from whole pages, and page 1
		// holds the root's own text, which would then leak into its first child and hide bug #30.
		const page3 = (await pageRows(api, first.sourceDocument)).find((row) => row.page_no === 3)!;
		const [, caption] = page3.canonical_markdown.match(IMAGE_TAG)!;
		const result = await api.call("wikify.api.pages.crop_page_figure", {
			source_document: first.sourceDocument,
			page_no: 3,
			caption,
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

	test.afterAll(async ({ api }) => {
		test.setTimeout(QUEUE_TIMEOUT);
		await deleteTestProjects(api, name);
	});

	test(
		"F-PUB-01 publish dialog previews the pages to be created",
		{
			tag: ["@functional", "@publish"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/44" },
		},
		async ({ page, api }) => {
			// known failure: #44. Remove test.fail() when the issue is closed.
			test.fail();
			const preview = await api.call("wikify.api.imports.preview_wiki", {
				import_name: first.import,
			});
			const dialog = await openPublishDialog(page, first.import);
			await expect(dialog).toContainText(
				new RegExp(`${FIXTURE_SECTIONS[0]}|\\b${preview.pages} pages?\\b`, "i"),
			);
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

			const leaf = publishedDocuments.find((document) => document.title === FIXTURE_SECTIONS[0])!;
			await page.goto(`/${leaf.route}`);
			await expect(
				page.getByRole("main").getByRole("heading", { level: 1, name: FIXTURE_SECTIONS[0] }),
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
			// known failure: #30. Remove test.fail() when the issue is closed.
			test.fail();
			const root = sections.find((row) => !row.parent_source_section)!;
			const paragraph = ownParagraph(root.markdown);
			expect(sections.filter((row) => row.markdown?.includes(paragraph))).toEqual([root]);
			const group = publishedDocuments.find((document) => document.name === root.wiki_document)!;
			await page.goto(`/${group.route}`);
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

			const leaf = publishedDocuments.find((document) => document.title === FIXTURE_SECTIONS[0])!;
			await page.goto(`/${leaf.route}`);
			const sidebar = page
				.getByRole("navigation")
				.filter({ has: page.getByRole("link", { name: FIXTURE_SECTIONS[0] }) });
			await expect(sidebar.getByRole("link", { name: FIXTURE_SECTIONS[6] })).toBeVisible();
			await expect(sidebar.getByText(excluded.title)).toHaveCount(0);
		},
	);

	test(
		"F-PUB-04 publish a second import into the existing space",
		{ tag: ["@functional", "@publish"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_TIMEOUT * 2);
			const { source_document: sourceDocument } = await waitForImport(
				api,
				second.import,
				"Review",
				QUEUE_TIMEOUT,
			);
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

			const newPage = added.find((document) => document.title === FIXTURE_SECTIONS[0])!;
			const oldPage = before.find((document) => document.title === FIXTURE_SECTIONS[0])!;
			for (const document of [newPage, oldPage]) {
				await page.goto(`/${document.route}`);
				const main = page.getByRole("main");
				await expect(
					main.getByRole("heading", { level: 1, name: FIXTURE_SECTIONS[0] }),
				).toBeVisible();
				const sidebar = page
					.getByRole("navigation")
					.filter({ has: page.getByRole("link", { name: FIXTURE_SECTIONS[0] }) });
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
				"BLOCKED: the dialog closes on Generate wiki and small imports generate in seconds; the only large import (R1) never reaches Review because its parse is killed at the 1 h RQ timeout.",
			);
		},
	);
});

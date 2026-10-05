import type { Locator, Page, Request } from "@playwright/test";
import type { Api } from "../helpers/api";
import { expect, test } from "../helpers/test";
import { FIXTURE_SECTIONS, findSection, setSectionMarkdown } from "../helpers/wikify";
import { waitFor } from "../helpers/wait";

type PageSnapshot = {
	name: string;
	page_no: number;
	canonical_markdown: string;
	canonical_composite: number;
	verdict: string;
};

type SectionSnapshot = {
	name: string;
	markdown: string;
	modified: string;
	lft: number;
	rgt: number;
	page_start: number;
	page_end: number;
};

type Snapshot = { pages: PageSnapshot[]; sections: SectionSnapshot[] };

const IMAGE_TAG = /!\[([^\]]*)\]\(([^)]*)\)/g;
// The crop job budget: propagation to sections runs on the one worker every agent shares.
const PROPAGATION_TIMEOUT = 900_000;
const PROPAGATION_JOB = "wikify.rag.events.propagate_dirty_pages";

function imageTags(markdown: string): { caption: string; url: string }[] {
	return [...(markdown || "").matchAll(IMAGE_TAG)].map((match) => ({
		caption: match[1],
		url: match[2],
	}));
}

async function takeSnapshot(api: Api, sourceDocument: string): Promise<Snapshot> {
	const pages = await api.getList<PageSnapshot>("Source Page", {
		filters: { source_document: sourceDocument },
		fields: ["name", "page_no", "canonical_markdown", "canonical_composite", "verdict"],
		orderBy: "page_no asc",
	});
	const sections = await api.getList<SectionSnapshot>("Source Section", {
		filters: { source_document: sourceDocument },
		fields: ["name", "markdown", "modified", "lft", "rgt", "page_start", "page_end"],
		orderBy: "lft asc",
	});
	return { pages, sections };
}

// Mirrors engine.sectionize.sections_covering_page: only the deepest covering sections are rebuilt.
function owningSections(sections: SectionSnapshot[], pageNo: number): SectionSnapshot[] {
	const covering = sections.filter(
		(section) => section.page_start <= pageNo && section.page_end >= pageNo,
	);
	return covering.filter(
		(row) => !covering.some((other) => other.lft > row.lft && other.rgt < row.rgt),
	);
}

async function propagationPending(api: Api, sourceDocument: string): Promise<boolean> {
	// RQ Job's virtual get_list needs an explicit order_by and a positive page length.
	const jobs = await api.call<{ arguments: string }[]>("frappe.client.get_list", {
		doctype: "RQ Job",
		filters: {
			queue: "long",
			status: ["in", ["queued", "started"]],
			job_name: PROPAGATION_JOB,
		},
		fields: ["arguments"],
		order_by: "creation desc",
		limit_page_length: 1000,
	});
	return jobs.some((job) => JSON.parse(job.arguments).kwargs?.source_document === sourceDocument);
}

// Waits until the crop's propagation job rebuilt the owning sections, or no such job is left
// (a site-wide cache clear drops the dirty-page entry, and then the sections are never rebuilt).
async function waitForPropagation(
	api: Api,
	sourceDocument: string,
	before: Snapshot,
	pageNos: number[],
): Promise<boolean> {
	const owners = [
		...new Set(pageNos.flatMap((pageNo) => owningSections(before.sections, pageNo))),
	];
	const rebuilt = async () => {
		const rows = await api.getList<SectionSnapshot>("Source Section", {
			filters: { name: ["in", owners.map((owner) => owner.name)] },
			fields: ["name", "modified"],
		});
		return owners.every(
			(owner) => rows.find((row) => row.name === owner.name)!.modified !== owner.modified,
		);
	};
	const state = await waitFor(
		async () => ({
			rebuilt: await rebuilt(),
			pending: await propagationPending(api, sourceDocument),
		}),
		(state) => state.rebuilt || !state.pending,
		{
			timeout: PROPAGATION_TIMEOUT,
			interval: 3_000,
			label: `sections of pages ${pageNos} rebuilt`,
		},
	);
	return state.rebuilt || (await rebuilt());
}

async function restoreCrops(api: Api, sourceDocument: string, before: Snapshot): Promise<void> {
	const now = await takeSnapshot(api, sourceDocument);
	const changedPages = now.pages.filter(
		(page) =>
			page.canonical_markdown !==
			before.pages.find((row) => row.name === page.name)!.canonical_markdown,
	);
	const croppedPages = changedPages.filter((page) => {
		const originalUrls = imageTags(
			before.pages.find((row) => row.name === page.name)!.canonical_markdown,
		).map((tag) => tag.url);
		return imageTags(page.canonical_markdown).some(
			(tag) => tag.url.includes("-crop") && !originalUrls.includes(tag.url),
		);
	});
	if (croppedPages.length) {
		await waitForPropagation(
			api,
			sourceDocument,
			before,
			croppedPages.map((page) => page.page_no),
		);
	}
	for (const page of changedPages) {
		const original = before.pages.find((row) => row.name === page.name)!;
		const originalUrls = imageTags(original.canonical_markdown).map((tag) => tag.url);
		for (const url of imageTags(page.canonical_markdown).map((tag) => tag.url)) {
			if (originalUrls.includes(url)) continue;
			const files = await api.getList("File", {
				filters: {
					file_url: url,
					attached_to_doctype: "Source Page",
					attached_to_name: page.name,
				},
			});
			for (const file of files) await api.delete("File", file.name);
		}
		await api.setValue("Source Page", page.name, {
			canonical_markdown: original.canonical_markdown,
			canonical_composite: original.canonical_composite,
			verdict: original.verdict,
		});
	}
	const sections = (await takeSnapshot(api, sourceDocument)).sections;
	await setSectionMarkdown(
		api,
		sourceDocument,
		before.sections.filter((original) =>
			sections.some((row) => row.name === original.name && row.markdown !== original.markdown),
		),
	);
	const after = await takeSnapshot(api, sourceDocument);
	expect(after.pages).toEqual(before.pages);
	expect(after.sections.map(({ name, markdown }) => ({ name, markdown }))).toEqual(
		before.sections.map(({ name, markdown }) => ({ name, markdown })),
	);
}

async function openPage(page: Page, importName: string, pageNo: number): Promise<void> {
	await page.goto(`/wikify/import/${importName}/pages?page=${pageNo}`);
}

function previewImage(page: Page, caption: string) {
	return page.getByRole("img", { name: caption, exact: true });
}

async function openCropDialog(page: Page, caption: string, image = previewImage(page, caption)) {
	await image.scrollIntoViewIfNeeded();
	await image.click();
	const dialog = page.getByRole("dialog");
	await expect(dialog.getByRole("heading", { name: `Fix '${caption}'` })).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Crop & embed" })).toBeEnabled();
	return dialog;
}

async function boundingBox(page: Page, selector: string) {
	const box = await page.locator(selector).boundingBox();
	if (!box) throw new Error(`${selector} has no bounding box`);
	return box;
}

async function drag(
	page: Page,
	from: { x: number; y: number },
	to: { x: number; y: number },
): Promise<void> {
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(to.x, to.y, { steps: 10 });
	await page.mouse.up();
}

function trackCropCalls(page: Page): Request[] {
	const calls: Request[] = [];
	page.on("request", (request) => {
		if (request.method() === "POST" && request.url().includes("wikify.api.pages.crop_page_figure"))
			calls.push(request);
	});
	return calls;
}

async function submitCrop(page: Page, dialog: Locator) {
	const response = page.waitForResponse(
		(response) =>
			response.url().includes("wikify.api.pages.crop_page_figure") &&
			response.request().method() === "POST",
	);
	await dialog.getByRole("button", { name: "Crop & embed" }).click();
	const cropped = await response;
	expect(cropped.ok()).toBe(true);
	await expect(dialog).toBeHidden();
	return cropped;
}

async function expectLoaded(image: Locator): Promise<void> {
	await image.scrollIntoViewIfNeeded();
	await expect
		.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth), {
			timeout: 30_000,
		})
		.toBeGreaterThan(0);
}

async function pageMarkdown(api: Api, sourceDocument: string, pageNo: number): Promise<string> {
	return api.getValue(
		"Source Page",
		{ source_document: sourceDocument, page_no: pageNo },
		"canonical_markdown",
	);
}

test.describe("crop", () => {
	let before: Snapshot;

	test.beforeEach(async ({ api, fixture }) => {
		before = await takeSnapshot(api, fixture.review.sourceDocument);
	});

	test.afterEach(async ({ api, fixture }) => {
		test.setTimeout(test.info().timeout + PROPAGATION_TIMEOUT);
		await restoreCrops(api, fixture.review.sourceDocument, before);
	});

	test(
		"F-CROP-01 crop a region of a figure",
		{ tag: ["@functional", "@crop", "@sanity"] },
		async ({ page, api, fixture }) => {
			const sourceDocument = fixture.review.sourceDocument;
			const [figure] = imageTags(
				before.pages.find((row) => row.page_no === 2)!.canonical_markdown,
			);
			const calls = trackCropCalls(page);

			await openPage(page, fixture.review.import, 2);
			const dialog = await openCropDialog(page, figure.caption);
			const selection = await boundingBox(page, "cropper-selection");
			const canvasImage = await boundingBox(page, "cropper-image");
			const centre = {
				x: selection.x + selection.width / 2,
				y: selection.y + selection.height / 2,
			};
			await drag(page, centre, { x: centre.x, y: canvasImage.y });
			const moved = await boundingBox(page, "cropper-selection");
			expect(moved.y).toBeLessThan(selection.y - selection.height / 4);
			await submitCrop(page, dialog);

			const body = calls[0].postDataJSON();
			expect(body.y0).toBeLessThan(0.05);
			expect(body.y1 - body.y0).toBeLessThan(0.75);
			expect(body.x1 - body.x0).toBeLessThan(0.75);
			const image = previewImage(page, figure.caption);
			await expect(image).toHaveAttribute("src", /^\/private\/files\/page-0002-crop\w*\.png$/);
			await expectLoaded(image);
			expect(calls).toHaveLength(1);
			const [tag] = imageTags(await pageMarkdown(api, sourceDocument, 2));
			expect(tag.caption).toBe(figure.caption);
			expect(tag.url).toMatch(/^\/private\/files\/page-0002-crop\w*\.png$/);
		},
	);

	test(
		"F-CROP-02 crop the full image",
		{ tag: ["@functional", "@crop"] },
		async ({ page, api, fixture }) => {
			const sourceDocument = fixture.review.sourceDocument;
			// Pages 3 and 5 have captions with double quotes, which the preview truncates; page 1 has none.
			const [figure] = imageTags(
				before.pages.find((row) => row.page_no === 1)!.canonical_markdown,
			);
			const calls = trackCropCalls(page);

			await openPage(page, fixture.review.import, 1);
			const dialog = await openCropDialog(page, figure.caption);
			const canvasImage = await boundingBox(page, "cropper-image");
			const topLeft = await boundingBox(page, 'cropper-handle[action="nw-resize"]');
			await drag(
				page,
				{ x: topLeft.x + topLeft.width / 2, y: topLeft.y + topLeft.height / 2 },
				{ x: canvasImage.x - 40, y: canvasImage.y - 40 },
			);
			const bottomRight = await boundingBox(page, 'cropper-handle[action="se-resize"]');
			await drag(
				page,
				{
					x: bottomRight.x + bottomRight.width / 2,
					y: bottomRight.y + bottomRight.height / 2,
				},
				{
					x: canvasImage.x + canvasImage.width + 40,
					y: canvasImage.y + canvasImage.height + 40,
				},
			);
			await submitCrop(page, dialog);
			await expect(page.getByRole("alert")).toHaveCount(0);

			const body = calls[0].postDataJSON();
			for (const edge of [body.x0, body.y0]) expect(edge).toBeCloseTo(0, 2);
			for (const edge of [body.x1, body.y1]) expect(edge).toBeCloseTo(1, 2);
			const image = previewImage(page, figure.caption);
			await expect(image).toHaveAttribute("src", /^\/private\/files\/page-0001-crop\w*\.png$/);
			await expectLoaded(image);
			expect(calls).toHaveLength(1);
			const [tag] = imageTags(await pageMarkdown(api, sourceDocument, 1));
			expect(tag.url).toMatch(/^\/private\/files\/page-0001-crop\w*\.png$/);
		},
	);

	test(
		"F-CROP-03 crop the second image on a page, only that one changes",
		{ tag: ["@functional", "@crop"] },
		async ({ page, api, fixture }) => {
			const sourceDocument = fixture.review.sourceDocument;
			// The fixture has no page with two same-caption images, so page 2's figure is duplicated (afterEach restores it).
			const original = before.pages.find((row) => row.page_no === 2)!;
			const [figure] = imageTags(original.canonical_markdown);
			const tag = `![${figure.caption}](${figure.url})`;
			await api.setValue("Source Page", original.name, {
				canonical_markdown: original.canonical_markdown.replace(tag, `${tag}\n\n${tag}`),
			});
			const calls = trackCropCalls(page);

			await openPage(page, fixture.review.import, 2);
			const images = previewImage(page, figure.caption);
			await expect(images).toHaveCount(2);
			const dialog = await openCropDialog(page, figure.caption, images.nth(1));
			await submitCrop(page, dialog);

			await expect(images.nth(1)).toHaveAttribute(
				"src",
				/^\/private\/files\/page-0002-crop\w*\.png$/,
			);
			await expect(images.nth(0)).toHaveAttribute("src", figure.url);
			expect(calls).toHaveLength(1);
			const tags = imageTags(await pageMarkdown(api, sourceDocument, 2));
			expect(tags.map((row) => row.caption)).toEqual([figure.caption, figure.caption]);
			expect(tags[0].url).toBe(figure.url);
			expect(tags[1].url).toMatch(/^\/private\/files\/page-0002-crop\w*\.png$/);
		},
	);

	test(
		"F-CROP-04 Wiki tab preview of the section that owns the cropped pages",
		{ tag: ["@functional", "@crop"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(2 * PROPAGATION_TIMEOUT + 120_000);
			const sourceDocument = fixture.review.sourceDocument;
			const section = await findSection(api, sourceDocument, FIXTURE_SECTIONS[0]);
			const crops: { caption: string; url: string }[] = [];
			for (const pageNo of [1, 2]) {
				const [figure] = imageTags(
					before.pages.find((row) => row.page_no === pageNo)!.canonical_markdown,
				);
				const result = await api.call("wikify.api.pages.crop_page_figure", {
					source_document: sourceDocument,
					page_no: pageNo,
					caption: figure.caption,
					occurrence: 0,
					x0: 0.25,
					y0: 0.25,
					x1: 0.75,
					y1: 0.75,
				});
				crops.push({ caption: figure.caption, url: result.image_url });
				// One page per propagation pass, so no pass is still queued when afterEach restores.
				expect(
					await waitForPropagation(api, sourceDocument, before, [pageNo]),
					`page ${pageNo} propagated`,
				).toBe(true);
			}
			await waitFor(
				() => api.getValue<string>("Source Section", section.name, "markdown"),
				(markdown) => crops.every((crop) => markdown.includes(crop.url)),
				{
					timeout: PROPAGATION_TIMEOUT,
					label: "section markdown has both crops",
				},
			);

			await page.goto(`/wikify/import/${fixture.review.import}/tree`);
			const row = page.locator(`[data-section-row="${section.name}"]`);
			await row.getByText(section.title, { exact: true }).click();
			const article = page.getByRole("article");
			await expect(article.getByRole("heading", { level: 1, name: section.title })).toBeVisible();
			for (const crop of crops) {
				const image = article.getByRole("img", {
					name: crop.caption,
					exact: true,
				});
				await expect(image).toHaveAttribute("src", crop.url);
				await expectLoaded(image);
			}
			const owners = await api.getList("Source Section", {
				filters: {
					source_document: sourceDocument,
					markdown: ["like", "%page-0002-crop%"],
				},
				fields: ["name"],
			});
			expect(owners.map((owner) => owner.name)).toContain(section.name);
		},
	);
});

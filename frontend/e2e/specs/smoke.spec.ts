import type { Locator, Page } from "@playwright/test";
import type { Api, Row } from "../helpers/api";
import { deleteTestProjects } from "../helpers/cleanup";
import { env, FIXTURE_PDF, PREFIX } from "../helpers/env";
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
import { pickInDialog } from "../helpers/upload";
import { waitFor } from "../helpers/wait";
import {
	FIXTURE_ROOT,
	FIXTURE_SECTIONS,
	pageRows,
	sectionRows,
	waitForImport,
} from "../helpers/wikify";

const stamp = Date.now();
const NAME = `${PREFIX} smoke ${stamp}`;
const SPACE_ROUTE = `test-smoke-${stamp}`;
// One background worker serves every agent's parses and jobs, so queues can be long.
const QUEUE_TIMEOUT = 1_800_000;
const IMAGE_TAG = /!\[([^\]]*)\]\(([^)]*)\)/g;

const run = {
	project: "",
	import: "",
	sourceDocument: "",
	renamed: "",
	renamedTitle: "",
	excluded: "",
};

// The start of the first plain paragraph, cut before any inline markup so it matches the rendered text.
function plainSnippet(markdown: string): string {
	for (const block of markdown.split(/\n\s*\n/)) {
		const text = block.trim().replace(/\s+/g, " ");
		if (/^(#|!\[|<|\||-|\*|>|\d)/.test(text)) continue;
		const plain = text.split(/[*_`[\]&<]/)[0].trim();
		if (plain.length >= 20) return plain.slice(0, 60);
	}
	throw new Error(`No plain paragraph in ${JSON.stringify(markdown.slice(0, 200))}`);
}

// The panel's icon-only buttons have no accessible name (frappe-ui sets aria-label from `label` only).
function iconButton(page: Page, icon: string): Locator {
	return page.getByRole("button").filter({ has: page.locator(`.${icon}`) });
}

async function waitForTurn(api: Api, sessionId: string, prompt: string): Promise<void> {
	await waitFor(
		() => api.call("wikify.api.agent.get_session", { session_id: sessionId }),
		({ session, messages }) => {
			const last = messages.at(-1);
			return (
				!session.is_running &&
				last?.role === "assistant" &&
				last.status !== "streaming" &&
				messages.some((row: Row) => row.content === prompt)
			);
		},
		{ timeout: QUEUE_TIMEOUT, interval: 3_000, label: `assistant turn in ${sessionId}` },
	);
}

test.describe("smoke", () => {
	test.describe.configure({ mode: "serial" });

	test.afterAll(async ({ api }) => {
		test.setTimeout(QUEUE_TIMEOUT);
		await deleteTestProjects(api, NAME);
	});

	test.describe("login", () => {
		test.use({ storageState: { cookies: [], origins: [] } });

		test(
			"S-01 open /wikify and log in",
			{ tag: ["@smoke", "@tree", "@sanity"] },
			async ({ page }) => {
				// Uncaught errors only, as `agent-browser errors` in the case: the SPA also opens frappe-ui's default
				// socket on port 9000, which fails on this bench (socketio_port 9012) and logs a resource error.
				const errors: string[] = [];
				page.on("pageerror", (error) => errors.push(error.message));
				await page.goto("/wikify");
				await expect(page).toHaveURL(/\/login\?redirect-to=(%2F|\/)wikify/);
				await page.getByRole("textbox", { name: "Email" }).fill(env.user);
				await page.getByLabel("Password", { exact: true }).fill(env.password);
				await page.getByRole("button", { name: "Continue" }).click();
				await expect(page).toHaveURL(/\/wikify/);

				await page.getByRole("link", { name: "Projects" }).click();
				await expect(page).toHaveURL(/\/wikify\/?$/);
				await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
				await expect(page.getByRole("button", { name: /^Uncategorized/ })).toBeVisible();
				expect(errors).toEqual([]);
			},
		);
	});

	test(
		"S-02 upload a PDF into a new project and parse it",
		{ tag: ["@smoke", "@upload"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_TIMEOUT + 300_000);
			await page.goto("/wikify/");
			await page.getByRole("button", { name: "New Project" }).first().click();
			const projectDialog = page.getByRole("dialog");
			await projectDialog.getByLabel("Project name").fill(NAME);
			await projectDialog.getByRole("button", { name: "Create" }).click();
			await expect(page).toHaveURL(/\/wikify\/project\/PRJ-[\d-]+$/);
			run.project = decodeURIComponent(page.url().split("/").pop()!);

			await page.getByRole("button", { name: "New Document" }).first().click();
			const dialog = page.getByRole("dialog");
			await expect(dialog.getByLabel("Project")).toContainText(NAME);
			await pickInDialog(page, FIXTURE_PDF);
			await dialog.getByLabel("Title").fill(NAME);
			await dialog.getByRole("button", { name: "Start", exact: true }).click();
			await expect(page).toHaveURL(/\/wikify\/import\/IMP-[\d-]+$/);
			run.import = decodeURIComponent(page.url().split("/").pop()!);
			await expect(page.getByRole("heading", { name: NAME })).toBeVisible();

			const imp = await waitForImport(api, run.import, "Review", QUEUE_TIMEOUT);
			expect(imp.error).toBeFalsy();
			expect(imp.stage_label).toBe("Parsed 6 pages");
			expect(await api.getValue("Wikify Import", run.import, ["project", "import_title"])).toEqual(
				{
					project: run.project,
					import_title: NAME,
				},
			);
			run.sourceDocument = imp.source_document;
		},
	);

	test(
		"S-03 Pages tab shows result markdown for every page",
		{ tag: ["@smoke", "@tree"] },
		async ({ page, api }) => {
			const pages = await pageRows(api, run.sourceDocument);
			expect(pages).toHaveLength(6);
			for (const row of pages)
				expect(row.canonical_markdown?.trim(), `page ${row.page_no}`).toBeTruthy();
			const flagged = pages.filter((row) => row.verdict !== "pass").length;

			await page.goto(`/wikify/import/${run.import}/pages`);
			await expect(page.getByRole("button", { name: "All (6)" })).toBeVisible();
			await expect(page.getByRole("button", { name: `Flagged (${flagged})` })).toBeVisible();
			await expect(page.getByRole("button", { name: `Passed (${6 - flagged})` })).toBeVisible();
			for (const row of pages) {
				await page.getByRole("button", { name: new RegExp(`^Page ${row.page_no}\\b`) }).click();
				await expect(page.getByText(plainSnippet(row.canonical_markdown))).toBeVisible();
			}
		},
	);

	test(
		"S-04 rename, exclude and drag persist after reload",
		{ tag: ["@smoke", "@tree"] },
		async ({ page, api }) => {
			const before = await sectionRows(api, run.sourceDocument);
			const byTitle = (title: string) => before.find((row) => row.title === title)!;
			const root = before.find((row) => !row.parent_source_section)!;
			expect(root.title).toBe(FIXTURE_ROOT);
			const children = childOrder(before, root.name);
			const renamed = byTitle(FIXTURE_SECTIONS[2]);
			const excluded = byTitle(FIXTURE_SECTIONS[3]);
			const moved = children.at(-1)!;
			const newTitle = `${NAME} renamed`;

			await openTree(page, run.import);
			// Drag on the fresh tree: dispatched after the row-menu edits, the drop sent no reorder_section.
			const reorderCall = waitForSectionCall(page, "reorder_section");
			await dragRow(page, moved, children[0], "before");
			expect((await reorderCall).ok()).toBe(true);

			await chooseRowAction(page, renamed.name, "Rename");
			const input = sectionRow(page, renamed.name).getByRole("textbox");
			await input.click();
			await input.fill(newTitle);
			const renameCall = waitForSectionCall(page, "rename_section");
			await input.press("Enter");
			expect((await renameCall).ok()).toBe(true);

			const toggleCall = waitForSectionCall(page, "toggle_include");
			await chooseRowAction(page, excluded.name, "Exclude from wiki");
			expect((await toggleCall).ok()).toBe(true);

			await page.reload();
			await expect(
				sectionRow(page, renamed.name).getByText(newTitle, { exact: true }),
			).toBeVisible();
			await expect(
				sectionRow(page, excluded.name).getByText(excluded.title, { exact: true }),
			).toHaveCSS("text-decoration-line", "line-through");
			expect(await rowOrder(page)).toEqual([root.name, moved, ...children.slice(0, -1)]);

			const after = await sectionRows(api, run.sourceDocument);
			const byName = Object.fromEntries(after.map((row) => [row.name, row]));
			expect(byName[renamed.name].title).toBe(newTitle);
			expect(byName[excluded.name].include_in_wiki).toBe(0);
			expect(childOrder(after, root.name)).toEqual([moved, ...children.slice(0, -1)]);
			run.renamed = renamed.name;
			run.renamedTitle = newTitle;
			run.excluded = excluded.name;
		},
	);

	test(
		"S-05 crop a figure, the page markdown gets the cropped image",
		{ tag: ["@smoke", "@crop"] },
		async ({ page, api }) => {
			const pageTwo = (await pageRows(api, run.sourceDocument)).find((row) => row.page_no === 2)!;
			const [[, caption, originalUrl]] = [...pageTwo.canonical_markdown.matchAll(IMAGE_TAG)];
			// A double quote in the caption breaks the UI crop (the preview truncates the alt text).
			expect(caption).not.toContain('"');

			await page.goto(`/wikify/import/${run.import}/pages?page=2`);
			const image = page.getByRole("img", { name: caption, exact: true });
			await image.scrollIntoViewIfNeeded();
			await image.click();
			const dialog = page.getByRole("dialog");
			await expect(dialog.getByRole("heading", { name: `Fix '${caption}'` })).toBeVisible();
			const cropped = page.waitForResponse(
				(response) =>
					response.url().includes("wikify.api.pages.crop_page_figure") &&
					response.request().method() === "POST",
			);
			await dialog.getByRole("button", { name: "Crop & embed" }).click();
			expect((await cropped).ok()).toBe(true);
			await expect(dialog).toBeHidden();

			await expect(image).toHaveAttribute("src", /^\/private\/files\/page-0002-crop\w*\.png$/);
			await expect
				.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth))
				.toBeGreaterThan(0);
			const markdown = await api.getValue("Source Page", pageTwo.name, "canonical_markdown");
			const tags = [...markdown.matchAll(IMAGE_TAG)];
			expect(tags).toHaveLength(1);
			expect(tags[0][1]).toBe(caption);
			expect(tags[0][2]).toMatch(/^\/private\/files\/page-0002-crop\w*\.png$/);
			expect(tags[0][2]).not.toBe(originalUrl);
		},
	);

	// Kept in the serial chain: S-07/S-08 need this import unpublished, and a retry of a serial group would
	// re-run the whole journey. So the test re-sends the prompt once if the model skipped the rename.
	test(
		"S-06 assistant renames a section, the tree updates without a reload",
		{ tag: ["@smoke", "@agent", "@llm"] },
		async ({ page, api }) => {
			test.setTimeout(2 * QUEUE_TIMEOUT);
			const before = await sectionRows(api, run.sourceDocument);
			const target = before.find((row) => row.title === FIXTURE_SECTIONS[0])!;
			const newTitle = `${NAME} renamed by agent`;
			const prompt = `${NAME} Rename section "${target.title}" to "${newTitle}"`;

			await openTree(page, run.import);
			await page.evaluate(() => ((window as any).__noReload = "smoke"));
			await iconButton(page, "lucide-sparkles").click();
			const panel = page.getByRole("complementary").filter({ hasText: "Assistant" });
			const input = panel.getByPlaceholder("Ask the assistant…");
			for (let attempt = 1; attempt <= 2; attempt++) {
				await input.fill(prompt);
				await input.press("Enter");
				const [session] = await waitFor(
					() =>
						api.getList("Wikify Agent Session", {
							filters: { source_document: run.sourceDocument, title: prompt.slice(0, 120) },
						}),
					(rows) => rows.length > 0,
					{ timeout: 60_000, label: "agent session" },
				);
				await waitForTurn(api, session.name, prompt);
				if ((await api.getValue("Source Section", target.name, "title")) === newTitle) break;
			}

			await expect(
				sectionRow(page, target.name).getByText(newTitle, { exact: true }),
			).toBeVisible();
			await expect(panel.getByText("Applied 1 change")).toBeVisible();
			expect(await page.evaluate(() => (window as any).__noReload)).toBe("smoke");
			const after = await sectionRows(api, run.sourceDocument);
			const shape = (rows: Row[]) =>
				rows.map(({ name, parent_source_section, sort_order }) => ({
					name,
					parent_source_section,
					sort_order,
				}));
			expect(shape(after)).toEqual(shape(before));
			expect(after.map((row) => (row.name === target.name ? target.title : row.title))).toEqual(
				before.map((row) => row.title),
			);
			expect(after.find((row) => row.name === target.name)!.title).toBe(newTitle);
		},
	);

	test("S-07 Approve & Build Graph", { tag: ["@smoke", "@tree"] }, async ({ page, api }) => {
		await openTree(page, run.import);
		const graphed = waitForSectionCall(page, "build_graph");
		await page.getByRole("button", { name: "Approve & Build Graph" }).click();
		expect((await graphed).ok()).toBe(true);
		await expect(page.getByRole("button", { name: "Publish", exact: true })).toBeEnabled();
		const imp = await waitForImport(api, run.import, "Graphed", 60_000);
		expect(imp.error).toBeFalsy();
	});

	test("S-08 publish to a new space", { tag: ["@smoke", "@publish"] }, async ({ page, api }) => {
		test.setTimeout(QUEUE_TIMEOUT + 300_000);
		await openTree(page, run.import);
		await page.getByRole("button", { name: "Publish", exact: true }).click();
		const dialog = page.getByRole("dialog");
		await expect(dialog.getByText("Publish wiki")).toBeVisible();
		await dialog.getByRole("radio", { name: "New space" }).click();
		await dialog.getByLabel("Space name").fill(NAME);
		await dialog.getByLabel("Route").fill(SPACE_ROUTE);
		await dialog.getByRole("button", { name: "Generate wiki" }).click();
		await expect(dialog).toBeHidden();

		const imp = await waitForImport(api, run.import, "Completed", QUEUE_TIMEOUT);
		expect(imp.error).toBeFalsy();
		const space = (await api.getList("Wiki Space", { filters: { route: SPACE_ROUTE } }))[0]?.name;
		expect(imp.wiki_space).toBe(space);

		const documents = await api.getList("Wiki Document", {
			filters: { route: ["like", `${SPACE_ROUTE}/%`] },
			fields: ["name", "title", "route"],
		});
		const sections = await sectionRows(api, run.sourceDocument);
		const included = sections.filter((row) => row.include_in_wiki);
		expect(documents).toHaveLength(included.length + 1);
		expect(sections.find((row) => row.name === run.excluded)!.wiki_document).toBeFalsy();

		const wikiDocument = sections.find((row) => row.name === run.renamed)!.wiki_document;
		const leaf = documents.find((row) => row.name === wikiDocument)!;
		await page.goto(`/${leaf.route}`);
		await expect(
			page.getByRole("main").getByRole("heading", { level: 1, name: run.renamedTitle }),
		).toBeVisible();
	});

	test(
		"S-09 Ask a question scoped to the project",
		{ tag: ["@smoke", "@ask", "@llm"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_TIMEOUT + 300_000);
			await waitFor(
				() => api.call("wikify.api.rag.index_status", { project: run.project }),
				(status) => status.documents === 1,
				{ timeout: QUEUE_TIMEOUT, interval: 5_000, label: `${run.project} indexed` },
			);
			const question = `${NAME} Which AI service does Wikify use, and where does its key live?`;

			await page.goto("/wikify/ask");
			await page.getByRole("combobox").click();
			await page.getByRole("option", { name: NAME, exact: true }).click();
			await expect(page.getByRole("combobox")).toHaveText(NAME);
			const composer = page.getByPlaceholder("Ask a question of this wiki…");
			await composer.fill(question);
			await composer.press("Enter");

			await expect(page).toHaveURL(/\/wikify\/ask\/ASK-[\d-]+$/, { timeout: 300_000 });
			const session = decodeURIComponent(page.url().split("/").pop()!);
			expect(await api.getValue("Wikify Ask Session", session, "project")).toBe(run.project);
			const [answer] = await api.getList("Wikify Ask Message", {
				filters: { session, role: "answer" },
				fields: ["refused", "citations"],
			});
			expect(answer.refused).toBe(0);
			const citations: Row[] = JSON.parse(answer.citations || "[]");
			expect(citations.length).toBeGreaterThan(0);
			for (const citation of citations) {
				expect(citation.wikify_import).toBe(run.import);
				expect(citation.source_document).toBe(run.sourceDocument);
			}
			const sources = page
				.getByRole("article")
				.getByRole("button", { name: new RegExp(`^${citations.length} sources? · 1 document$`) });
			await expect(sources).toBeVisible();
		},
	);
});

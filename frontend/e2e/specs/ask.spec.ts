import type { Page, Response } from "@playwright/test";
import type { Api } from "../helpers/api";
import { deleteByPrefix } from "../helpers/cleanup";
import { PREFIX } from "../helpers/env";
import { expect, test } from "../helpers/test";
import { escapeRegExp } from "../helpers/wikify";

const AREA_PREFIX = `${PREFIX} ask ${Date.now()}`;
const ANSWER_TIMEOUT = 180_000;

type Counts = Record<string, number>;

async function pickProject(page: Page, projectName: string): Promise<void> {
	await page.getByRole("combobox").click();
	await page.getByRole("option", { name: projectName, exact: true }).click();
	await expect(page.getByRole("combobox")).toHaveText(projectName);
}

async function typeLabels(api: Api): Promise<Record<string, string>> {
	const types = await api.getList("Section Type", {
		fields: ["type_name", "label"],
		orderBy: "creation asc",
	});
	return Object.fromEntries(types.map((type) => [type.type_name, type.label || type.type_name]));
}

// Section counts keyed by Section Type label ("Untagged" for none), and per source document for each label.
async function sectionCounts(api: Api, sourceDocuments: string[], labels: Record<string, string>) {
	const rows = await api.getList("Source Section", {
		filters: { source_document: ["in", sourceDocuments] },
		fields: ["source_document", "section_type"],
	});
	const byType: Counts = {};
	const byDocument: Record<string, Counts> = {};
	for (const row of rows) {
		const label = row.section_type ? labels[row.section_type] : "Untagged";
		byType[label] = (byType[label] || 0) + 1;
		byDocument[label] ??= {};
		byDocument[label][row.source_document] = (byDocument[label][row.source_document] || 0) + 1;
	}
	return { total: rows.length, byType, byDocument };
}

function taggedOnly(byType: Counts): Counts {
	return Object.fromEntries(Object.entries(byType).filter(([label]) => label !== "Untagged"));
}

// Reads "<label> <count>" buttons (Explore type rail, graph legend chips) for every known label.
async function labelledCounts(page: Page, labels: string[]): Promise<Counts> {
	const counts: Counts = {};
	for (const label of new Set(labels)) {
		const buttons = page.getByRole("button", {
			name: new RegExp(`^${escapeRegExp(label)} (\\d+)$`),
		});
		for (const name of await buttons.allInnerTexts()) {
			const count = name.match(/(\d+)\s*$/)?.[1];
			if (count) counts[label] = Number(count);
		}
	}
	return counts;
}

function nodeKinds(body: any): Counts {
	const counts: Counts = {};
	for (const node of body.data.nodes) counts[node.kind] = (counts[node.kind] || 0) + 1;
	return counts;
}

function isGraphResponse(method: string) {
	return (response: Response) =>
		response.url().includes(`wikify.api.graph.${method}`) && response.ok();
}

async function askViaApi(api: Api, question: string, project: string): Promise<string> {
	const result = await api.call("wikify.api.rag.ask", { question, project });
	expect(result.refused, "the fixture answers this question").toBeFalsy();
	return result.session;
}

test.describe("ask", () => {
	test.afterAll(async ({ api }) => {
		await deleteByPrefix(api, "Wikify Ask Session", "title", AREA_PREFIX);
	});

	test.describe("answers", () => {
		test.describe.configure({ retries: 1 });

		test(
			"F-ASK-01 ask a question scoped to a project",
			{ tag: ["@functional", "@llm", "@ask"] },
			async ({ page, api, fixture }) => {
				test.setTimeout(ANSWER_TIMEOUT + 60_000);
				const topic = fixture.review.outline.children[1].title;
				const question = `${AREA_PREFIX} What does the document say about "${topic}"?`;

				await page.goto("/wikify/ask");
				await pickProject(page, fixture.projectName);
				const composer = page.getByPlaceholder("Ask a question of this wiki…");
				await composer.fill(question);
				await composer.press("Enter");

				await expect(page).toHaveURL(/\/wikify\/ask\/ASK-[\d-]+$/, { timeout: ANSWER_TIMEOUT });
				const session = decodeURIComponent(page.url().split("/").pop()!);

				const conversation = await api.getValue("Wikify Ask Session", session, [
					"project",
					"title",
				]);
				expect(conversation).toEqual({ project: fixture.project, title: question });
				const answers = await api.getList("Wikify Ask Message", {
					filters: { session, role: "answer" },
					fields: ["refused", "citations", "content"],
				});
				expect(answers).toHaveLength(1);
				expect(answers[0].refused).toBe(0);
				expect(answers[0].content).not.toBe("");
				const citations: any[] = JSON.parse(answers[0].citations || "[]");
				expect(citations.length).toBeGreaterThan(0);
				const fixtureDocuments = [fixture.review.sourceDocument, fixture.agent.sourceDocument];
				const fixtureImports = [fixture.review.import, fixture.agent.import];
				for (const citation of citations) {
					expect(fixtureDocuments).toContain(citation.source_document);
					expect(fixtureImports).toContain(citation.wikify_import);
				}

				const turn = page.getByRole("article");
				await expect(turn.getByText(question)).toBeVisible();
				await expect(turn.getByRole("button", { name: /^\d+$/ }).first()).toBeVisible();
				const documents = new Set(citations.map((citation) => citation.source_document)).size;
				const sourcesButton = turn.getByRole("button", {
					name: new RegExp(`^${citations.length} sources? · ${documents} documents?$`),
				});
				await expect(sourcesButton).toBeVisible();
				await sourcesButton.click();
				await expect(sourcesButton).toHaveAttribute("aria-expanded", "true");
			},
		);

		test(
			"F-ASK-02 reopen from Ask history, then delete",
			{
				tag: ["@functional", "@llm", "@ask"],
				annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/53" },
			},
			async ({ page, api, fixture }) => {
				test.setTimeout(ANSWER_TIMEOUT + 60_000);
				const topic = fixture.review.outline.children[2].title;
				const question = `${AREA_PREFIX} What does the document say about "${topic}"?`;
				const countBefore = await api.call("frappe.client.get_count", {
					doctype: "Wikify Ask Session",
					filters: { project: fixture.project },
				});
				const session = await askViaApi(api, question, fixture.project);

				await page.goto("/wikify/ask");
				await pickProject(page, fixture.projectName);
				await expect(page.getByPlaceholder("Ask a question of this wiki…")).toBeVisible();
				// frappe-ui's Button overwrites aria-label with its (empty) label prop, so the icon-only
				// history button has no accessible name; find it by its icon.
				const history = page.getByRole("heading", { name: "History" });
				if (!(await history.isVisible()))
					await page.locator("button:has(.lucide-history)").click();
				await expect(history).toBeVisible();

				await page.getByRole("button", { name: new RegExp(`^${escapeRegExp(question)}`) }).click();
				await expect(page).toHaveURL(new RegExp(`/wikify/ask/${session}$`));
				const turn = page.getByRole("article");
				await expect(turn.getByText(question)).toBeVisible();
				await expect(
					turn.getByRole("button", { name: /^\d+ sources? · \d+ documents?$/ }),
				).toBeVisible();

				const row = page.getByRole("listitem").filter({ hasText: question });
				await row.getByRole("button", { name: /^Delete conversation: / }).click();
				const confirm = page.getByRole("dialog");
				await expect(confirm).toBeVisible();
				await expect(confirm).toContainText(`Delete "${question}" and its answers?`);
				expect(
					await api.call("frappe.client.get_count", {
						doctype: "Wikify Ask Session",
						filters: { name: session },
					}),
				).toBe(1);
				await confirm.getByRole("button", { name: /Delete/ }).click();

				await expect(row).toHaveCount(0);
				expect(
					await api.call("frappe.client.get_count", {
						doctype: "Wikify Ask Session",
						filters: { project: fixture.project },
					}),
				).toBe(countBefore);
				expect(
					await api.call("frappe.client.get_count", {
						doctype: "Wikify Ask Message",
						filters: { session },
					}),
				).toBe(0);
			},
		);
	});

	// Another spec reclassifies fixture.agent sections while this runs, so the UI must match the DB
	// as read just before or just after it.
	test(
		"F-ASK-03 Explore section-type counts equal the DB",
		{ tag: ["@functional", "@ask", "@sanity"] },
		async ({ page, api, fixture }) => {
			const labels = await typeLabels(api);
			const documents = [fixture.review.sourceDocument, fixture.agent.sourceDocument];
			const titles = {
				[fixture.review.sourceDocument]: fixture.review.title,
				[fixture.agent.sourceDocument]: fixture.agent.title,
			};

			await expect(async () => {
				await page.goto("/wikify/explore");
				await expect(page.getByText("Sections by type across documents")).toBeVisible();
				const before = await sectionCounts(api, documents, labels);
				const summary = page.waitForResponse(
					(response) =>
						response.url().includes("explore.type_summary") &&
						response.url().includes(fixture.project),
				);
				await pickProject(page, fixture.projectName);
				await summary;
				await expect(page.getByText("Types", { exact: true })).toBeVisible();
				const shown = await labelledCounts(page, [...Object.values(labels), "Untagged"]);
				const after = await sectionCounts(api, documents, labels);
				expect([before.byType, after.byType]).toContainEqual(shown);

				const snapshot = JSON.stringify(shown) === JSON.stringify(before.byType) ? before : after;
				const type = snapshot.byType["Research & Documentation"]
					? "Research & Documentation"
					: Object.keys(shown)[0];
				await page.getByRole("button", { name: `${type} ${shown[type]}`, exact: true }).click();
				await expect(page.getByRole("heading", { name: type })).toBeVisible();
				for (const [sourceDocument, count] of Object.entries(snapshot.byDocument[type])) {
					await expect(
						page.getByRole("button", { name: `${titles[sourceDocument]} ${count}`, exact: true }),
					).toBeVisible();
				}
			}).toPass({ timeout: 90_000 });
		},
	);

	test(
		"F-ASK-04 document graph and project graph",
		{ tag: ["@functional", "@ask", "@sanity"] },
		async ({ page, api, fixture }) => {
			const labels = await typeLabels(api);
			const allLabels = Object.values(labels);
			const sourceDocument = fixture.review.sourceDocument;

			await expect(async () => {
				await page.goto(`/wikify/import/${fixture.review.import}`);
				const graph = page.waitForResponse(isGraphResponse("get_document_graph"));
				await page.getByRole("link", { name: "Graph" }).click();
				await expect(page).toHaveURL(new RegExp(`/wikify/import/${fixture.review.import}/graph$`));
				const before = await sectionCounts(api, [sourceDocument], labels);
				const kinds = nodeKinds(await (await graph).json());
				await expect(page.locator("canvas")).toBeVisible();
				const legend = await labelledCounts(page, allLabels);
				const after = await sectionCounts(api, [sourceDocument], labels);
				expect([before.total, after.total]).toContain(kinds.section);
				expect(kinds.document).toBe(1);
				expect([taggedOnly(before.byType), taggedOnly(after.byType)]).toContainEqual(legend);
			}).toPass({ timeout: 90_000 });

			await expect(async () => {
				await page.goto(`/wikify/project/${fixture.project}`);
				const graph = page.waitForResponse(isGraphResponse("get_project_graph"));
				await page.getByRole("link", { name: "Graph" }).click();
				await expect(page).toHaveURL(new RegExp(`/wikify/project/${fixture.project}/graph$`));
				const documents = await api.getList("Source Document", {
					filters: { project: fixture.project },
				});
				const before = await sectionCounts(
					api,
					documents.map((row) => row.name),
					labels,
				);
				const kinds = nodeKinds(await (await graph).json());
				await expect(page.locator("canvas")).toBeVisible();
				await expect(page.getByRole("combobox")).toHaveText("All documents");
				const legend = await labelledCounts(page, allLabels);
				const after = await sectionCounts(
					api,
					documents.map((row) => row.name),
					labels,
				);
				expect(kinds.document).toBe(documents.length);
				expect([before.total, after.total]).toContain(kinds.section);
				expect([taggedOnly(before.byType), taggedOnly(after.byType)]).toContainEqual(legend);
			}).toPass({ timeout: 90_000 });
		},
	);
});

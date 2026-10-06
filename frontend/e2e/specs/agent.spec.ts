import type { Locator, Page } from "@playwright/test";
import type { Api, Row } from "../helpers/api";
import { iconButton, waitForTurn } from "../helpers/assistant";
import { PREFIX } from "../helpers/env";
import { expect, type FixtureOutline, type OutlineEntry, test } from "../helpers/test";
import { openTree, rowOrder, sectionRow } from "../helpers/tree";
import { waitFor } from "../helpers/wait";
import {
	findFigure,
	propagationPending,
	type SectionRow,
	sectionRows,
	setSectionMarkdown,
} from "../helpers/wikify";

// Turns share the single worker with every parse on the site, so a turn can queue for minutes.
const TURN_TIMEOUT = 600_000;
const TEST_TIMEOUT = 720_000;
const createdSessions = new Set<string>();

type Turn = { session: Row; messages: Row[] };

async function openAssistant(page: Page, importName: string): Promise<Locator> {
	await openTree(page, importName);
	await iconButton(page, "lucide-sparkles").click();
	const panel = page.getByRole("complementary").filter({ hasText: "Assistant" });
	await expect(panel.getByPlaceholder("Ask the assistant…")).toBeVisible();
	return panel;
}

async function sendPrompt(panel: Locator, text: string): Promise<void> {
	const input = panel.getByPlaceholder("Ask the assistant…");
	await input.fill(text);
	await input.press("Enter");
}

async function findSession(api: Api, sourceDocument: string, title: string): Promise<string> {
	const rows = await waitFor(
		() =>
			api.getList("Wikify Agent Session", {
				filters: { source_document: sourceDocument, title: title.slice(0, 120) },
				fields: ["name"],
			}),
		(found) => found.length > 0,
		{ timeout: 60_000, label: `agent session titled "${title}"` },
	);
	createdSessions.add(rows[0].name);
	return rows[0].name;
}

async function readSession(api: Api, sessionId: string): Promise<Turn> {
	const result = await api.call("wikify.api.agent.get_session", { session_id: sessionId });
	return { session: result.session, messages: result.messages };
}

function toolRows(rows: Row[], names: string[]): Row[] {
	return rows.filter((row) => row.role === "tool" && names.includes(row.tool_name));
}

function finalReply(rows: Row[]): Row {
	return rows.filter((row) => row.role === "assistant").at(-1)!;
}

async function settle(api: Api, sessionId: string): Promise<void> {
	await waitFor(
		() => readSession(api, sessionId),
		({ session }) => !session.is_running,
		{ timeout: TURN_TIMEOUT, interval: 3_000, label: `${sessionId} idle` },
	).catch((error) => console.warn(`cleanup: ${error.message}`));
}

function toolArgs(row: Row): Row {
	return JSON.parse(row.metadata_json || "{}").args || {};
}

async function createTestSection(
	api: Api,
	sourceDocument: string,
	title: string,
	markdown: string,
): Promise<string> {
	return (
		await api.call("wikify.api.sections.create_section", {
			source_document: sourceDocument,
			title,
			markdown,
		})
	).name;
}

async function deleteStampedSections(
	api: Api,
	sourceDocument: string,
	stamp: number,
): Promise<void> {
	const rows = await api.getList("Source Section", {
		filters: { source_document: sourceDocument, title: ["like", `${PREFIX} agent ${stamp}%`] },
		orderBy: "lft desc",
	});
	for (const row of rows) {
		await api
			.call("wikify.api.sections.delete_section", { name: row.name })
			.catch((error) => console.warn(`cleanup: ${error.message}`));
	}
}

async function pageSnapshot(api: Api, sourceDocument: string): Promise<Row[]> {
	return api.getList("Source Page", {
		filters: { source_document: sourceDocument },
		fields: ["name", "page_no", "canonical_markdown", "canonical_composite", "verdict"],
		orderBy: "page_no asc",
	});
}

async function waitForPropagation(api: Api, sourceDocument: string): Promise<void> {
	await waitFor(
		() => propagationPending(api, sourceDocument),
		(pending) => !pending,
		{ timeout: TURN_TIMEOUT, interval: 3_000, label: `page propagation for ${sourceDocument}` },
	);
}

// The page writes go straight to the row, so they don't queue another propagation pass.
async function restoreContent(
	api: Api,
	sourceDocument: string,
	sections: SectionRow[],
	pages: Row[],
): Promise<void> {
	for (const page of await pageSnapshot(api, sourceDocument)) {
		const original = pages.find((row) => row.name === page.name)!;
		if (JSON.stringify(page) !== JSON.stringify(original)) {
			const { canonical_markdown, canonical_composite, verdict } = original;
			await api.setValue("Source Page", page.name, {
				canonical_markdown,
				canonical_composite,
				verdict,
			});
		}
	}
	const current = await sectionRows(api, sourceDocument);
	await setSectionMarkdown(
		api,
		sourceDocument,
		sections.filter((original) =>
			current.some((row) => row.name === original.name && row.markdown !== original.markdown),
		),
	);
}

function treeShape(rows: SectionRow[]) {
	return rows.map(({ name, title, parent_source_section }) => ({
		name,
		title,
		parent_source_section,
	}));
}

// A run of plain words that occurs once in the markdown, so a prompt can quote it and the render shows it as is.
function plainPhrase(markdown: string, words = 4): string | undefined {
	for (const line of (markdown || "").split("\n")) {
		if (/^\s*([#!|>]|[-*]\s|\d+\.)/.test(line)) continue;
		const tokens = line.trim().split(/\s+/);
		for (let start = 0; start + words <= tokens.length; start++) {
			const run = tokens.slice(start, start + words);
			if (!run.every((token) => /^[A-Za-z][A-Za-z'-]*$/.test(token))) continue;
			const phrase = run.join(" ");
			if (markdown.split(phrase).length === 2) return phrase;
		}
	}
}

// A word in exactly one section body and in no title, so only a search tool can place it.
function bodyOnlyWord(sections: SectionRow[]): { word: string; owner: SectionRow } | undefined {
	const bodies = sections.map((row) =>
		(row.markdown || "").replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\]\([^)]*\)/g, "]"),
	);
	const titles = sections.map((row) => row.title.toLowerCase()).join("\n");
	const words = new Set(
		bodies.flatMap((body) => body.match(/\b[A-Za-z][A-Za-z0-9]{5,}\b/g) || []),
	);
	const rank = (word: string) => (/[a-z][A-Z]/.test(word) ? 0 : /^[A-Z]/.test(word) ? 1 : 2);
	for (const word of [...words].sort((a, b) => rank(a) - rank(b))) {
		const lower = word.toLowerCase();
		if (titles.includes(lower)) continue;
		const owners = sections.filter((_, index) => bodies[index].toLowerCase().includes(lower));
		if (owners.length === 1) return { word, owner: owners[0] };
	}
}

// A figure whose tag points at its page image, embedded in one of the outline's sections.
async function pageImageFigure(
	api: Api,
	sourceDocument: string,
	sections: SectionRow[],
	{ children }: FixtureOutline,
) {
	for (const child of children) {
		const owner = sections.find((row) => row.name === child.name)!;
		const figure = await findFigure(api, sourceDocument, {
			fromPage: owner.page_start,
			toPage: owner.page_end,
			plainCaption: true,
		}).catch(() => undefined);
		if (!figure) continue;
		const image: string = await api.getValue("Source Page", figure.page, "image");
		if (figure.url === image && owner.markdown.includes(`![${figure.caption}](${image})`))
			return { owner, image, ...figure };
	}
}

function outlineRows(sections: SectionRow[], { root, children }: FixtureOutline): SectionRow[] {
	const names = [...(root ? [root] : []), ...children].map((entry) => entry.name);
	return sections.filter((row) => names.includes(row.name));
}

async function expectPristine(
	api: Api,
	sourceDocument: string,
	sections: SectionRow[],
): Promise<void> {
	const after = await sectionRows(api, sourceDocument);
	expect(treeShape(after)).toEqual(treeShape(sections));
	expect(after.every((row) => row.include_in_wiki === 1)).toBe(true);
	expect(after.map(({ name, markdown }) => ({ name, markdown }))).toEqual(
		sections.map(({ name, markdown }) => ({ name, markdown })),
	);
}

test.describe("assistant", () => {
	test.describe.configure({ retries: 1 });

	test.afterAll(async ({ api }) => {
		test.setTimeout(TURN_TIMEOUT + 60_000);
		for (const sessionId of createdSessions) {
			await settle(api, sessionId);
			await api
				.delete("Wikify Agent Session", sessionId)
				.catch((error) => console.warn(`cleanup: ${error.message}`));
		}
		createdSessions.clear();
	});

	// The local Claude CLI backend sometimes tries a native tool call, gets "No such tool available" and
	// answers without the tool (about 1 in 3 turns).
	test.describe(() => {
		test.describe.configure({ retries: 2 });

		test(
			"F-AGENT-01 show me the section tree",
			{ tag: ["@functional", "@llm", "@agent", "@sanity"] },
			async ({ page, api, fixture }) => {
				test.setTimeout(TEST_TIMEOUT);
				const { sourceDocument } = fixture.agent;
				const prompt = `${PREFIX} agent ${Date.now()} Show me the section tree`;
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				const sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				const treeCalls = toolRows(rows, ["read_tree"]);
				expect(treeCalls.length).toBeGreaterThan(0);
				const sections = await sectionRows(api, sourceDocument);
				const { root, children } = fixture.agent.outline;
				expect(outlineRows(sections, fixture.agent.outline).map((row) => row.title)).toEqual(
					[...(root ? [root] : []), ...children].map((entry) => entry.title),
				);
				// The stored tool result loses its <section id> tags (Long Text is HTML-sanitised), so match titles and pages.
				const treeText = treeCalls.at(-1)!.content as string;
				for (const section of sections) {
					const pages =
						section.page_end === section.page_start
							? `${section.page_start}`
							: `${section.page_start}-${section.page_end}`;
					expect(treeText).toContain(`${section.title} (${section.section_type}) [p.${pages}]`);
				}
				await expect(panel.getByText("read_tree", { exact: true }).first()).toBeVisible();
			},
		);

		test(
			"F-AGENT-02 find sections about a topic",
			{ tag: ["@functional", "@llm", "@agent", "@sanity"] },
			async ({ page, api, fixture }) => {
				test.setTimeout(TEST_TIMEOUT);
				const { sourceDocument } = fixture.agent;
				const found = bodyOnlyWord(await sectionRows(api, sourceDocument));
				test.skip(!found, "no word of this parse sits in exactly one section body and no title");
				const { word, owner } = found!;
				const prompt = `${PREFIX} agent ${Date.now()} Use your search tool to find the sections of this document that mention "${word}", then list their titles.`;
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				const sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				const searches = toolRows(rows, ["semantic_search", "search_sections"]);
				expect(searches.length).toBeGreaterThan(0);
				const results = searches.map((row) => row.content).join("\n");
				expect(results, `search results for "${word}" name "${owner.title}"`).toContain(
					owner.title,
				);
			},
		);
	});

	test(
		"F-AGENT-03 move, rename, set type of a section",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const { root, children } = fixture.agent.outline;
			test.skip(!root, "this parse has no root section to move a child out of");
			const before = await sectionRows(api, sourceDocument);
			const row = (entry: OutlineEntry) => before.find((section) => section.name === entry.name)!;
			const movedIndex = 3;
			const [renamed, retyped, moved] = [children[1], children[2], children[movedIndex]].map(row);
			const newTitle = `${PREFIX} agent renamed ${stamp}`;
			const newType =
				retyped.section_type === "training_and_education"
					? "quality_and_audits"
					: "training_and_education";
			const prompt =
				`${PREFIX} agent ${stamp} Do three things: 1) move the section '${moved.title}' (currently under '${root!.title}') to the top level of the tree; ` +
				`2) rename the section '${renamed.title}' to '${newTitle}'; 3) set the section type of '${retyped.title}' to '${newType}'.`;
			let sessionId = "";
			try {
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				for (const tool of ["move_section", "rename_section", "set_section_type"]) {
					const calls = toolRows(rows, [tool]);
					expect(calls, `${tool} rows`).toHaveLength(1);
					expect(calls[0].content).not.toMatch(/fail|couldn't|not found/i);
				}

				const after = await sectionRows(api, sourceDocument);
				const byName = Object.fromEntries(after.map((row) => [row.name, row]));
				expect(byName[moved.name].parent_source_section).toBeNull();
				expect(byName[renamed.name].title).toBe(newTitle);
				expect(byName[retyped.name].section_type).toBe(newType);

				const movedRow = page
					.locator(`[data-section-row="${moved.name}"]`)
					.locator("xpath=ancestor::*[@role='treeitem'][1]");
				await expect(movedRow).toHaveAttribute("aria-level", "1", { timeout: 30_000 });
				await expect(page.locator(`[data-section-row="${renamed.name}"]`)).toContainText(newTitle);
				await expect(page.locator(`[data-section-row="${retyped.name}"]`)).toContainText(newType);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await api.call("wikify.api.sections.move_section", {
					name: moved.name,
					new_parent: root!.name,
					new_index: movedIndex,
				});
				await api.call("wikify.api.sections.rename_section", {
					name: renamed.name,
					title: renamed.title,
				});
				await api.call("wikify.api.sections.set_section_type", {
					name: retyped.name,
					section_type: retyped.section_type,
				});
			}
			expect(treeShape(await sectionRows(api, sourceDocument))).toEqual(treeShape(before));
		},
	);

	// The agent works on [test] sections this test creates, so the fixture tree only gains rows it loses again.
	test(
		"F-AGENT-04 create a section; split one; merge two siblings",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const title = (suffix: string) => `${PREFIX} agent ${stamp} ${suffix}`;
			let sessionId = "";
			try {
				const splitSource = await createTestSection(
					api,
					sourceDocument,
					title("split source"),
					"Alpha paragraph stays in the first part.\n\nBeta paragraph starts the second part.",
				);
				const keep = await createTestSection(
					api,
					sourceDocument,
					title("merge keep"),
					"Kept body text.",
				);
				const absorbed = await createTestSection(
					api,
					sourceDocument,
					title("merge absorbed"),
					"Absorbed body text.",
				);
				const start = (await sectionRows(api, sourceDocument)).length;
				// Parsed sections carry no markdown headings and split_section needs one (#35), so the prompt asks for it first.
				const prompt =
					`${title("")}Do three things: 1) create a new top-level section titled '${title("created")}' with the text 'Created by the agent e2e test.'; ` +
					`2) in the section '${title("split source")}', insert a markdown heading '## Beta part' right before the paragraph that begins with 'Beta paragraph', ` +
					`then split the section at that heading, naming the second part '${title("split part")}'; ` +
					`3) merge the sibling sections '${title("merge keep")}' and '${title("merge absorbed")}', keeping '${title("merge keep")}'.`;
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				for (const tool of ["create_section", "split_section", "merge_sections"]) {
					expect(toolRows(rows, [tool]).length, `${tool} rows`).toBeGreaterThan(0);
				}

				const after = await sectionRows(api, sourceDocument);
				expect(after).toHaveLength(start + 1 + 1 - 1);
				const created = after.find((row) => row.title === title("created"));
				expect(created?.parent_source_section).toBeNull();
				expect(created?.markdown).toContain("Created by the agent e2e test.");
				const source = after.find((row) => row.name === splitSource)!;
				const part = after.find((row) => row.title === title("split part"));
				expect(part?.parent_source_section).toBe(source.parent_source_section);
				expect(after.indexOf(part!)).toBe(after.indexOf(source) + 1);
				expect(part?.markdown).toMatch(/^## Beta part\s+Beta paragraph starts the second part\./);
				expect(source.markdown).toContain("Alpha paragraph stays in the first part.");
				expect(source.markdown).not.toContain("Beta paragraph");
				expect(after.find((row) => row.name === absorbed)).toBeUndefined();
				const kept = after.find((row) => row.name === keep)!;
				expect(kept.markdown).toContain("Kept body text.");
				expect(kept.markdown).toContain("Absorbed body text.");

				await page.reload();
				await expect(
					page.getByText("Sections", { exact: true }).locator("xpath=following-sibling::*[1]"),
				).toHaveText(String(after.length));
				const createdRow = sectionRow(page, created!.name).locator(
					"xpath=ancestor::*[@role='treeitem'][1]",
				);
				await expect(createdRow).toHaveAttribute("aria-level", "1");
				const order = await rowOrder(page);
				expect(order.indexOf(part!.name)).toBe(order.indexOf(splitSource) + 1);
				await expect(sectionRow(page, absorbed)).toHaveCount(0);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await deleteStampedSections(api, sourceDocument, stamp);
			}
			await expectPristine(api, sourceDocument, before);
		},
	);

	test(
		"F-AGENT-05 delete a section (confirm-gated)",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT * 2);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const title = `${PREFIX} agent ${stamp} delete me`;
			const held = "[NOT EXECUTED — awaiting user confirmation]";
			let sessionId = "";
			try {
				const target = await createTestSection(
					api,
					sourceDocument,
					title,
					"A section the agent e2e test deletes.",
				);
				const exists = async () =>
					(await api.getList("Source Section", { filters: { name: target } })).length > 0;
				const prompt = `${PREFIX} agent ${stamp} Delete the section '${title}'.`;
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				let rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(
					toolRows(rows, ["delete_section"]).map((row) => row.content.startsWith(held)),
				).toEqual([true]);
				expect(await exists()).toBe(true);
				await expect(panel.getByText("Confirm delete_section")).toBeVisible();
				await expect(panel.getByText(`Delete section '${title}'.`, { exact: true })).toBeVisible();
				await panel.getByRole("button", { name: "Cancel" }).click();
				await expect(panel.getByText("Cancelled.")).toBeVisible();
				expect(await exists()).toBe(true);

				const typedYes = "Yes, go ahead and delete it.";
				await sendPrompt(panel, typedYes);
				rows = await waitForTurn(api, sessionId, typedYes, TURN_TIMEOUT);
				expect(
					toolRows(rows, ["delete_section"]).map((row) => row.content.startsWith(held)),
				).toEqual([true]);
				expect(await exists()).toBe(true);

				await panel.getByRole("button", { name: "Run it" }).click();
				rows = await waitForTurn(
					api,
					sessionId,
					"Yes — go ahead and run delete_section.",
					TURN_TIMEOUT,
				);
				const deletes = toolRows(rows, ["delete_section"]);
				expect(deletes).toHaveLength(1);
				expect(toolArgs(deletes[0]).name).toBe(target);
				expect(deletes[0].content).not.toContain(held);
				expect(await exists()).toBe(false);

				await page.reload();
				await expect(page.locator("[data-section-row]").first()).toBeVisible();
				await expect(page.getByText(title)).toHaveCount(0);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await deleteStampedSections(api, sourceDocument, stamp);
			}
			await expectPristine(api, sourceDocument, before);
		},
	);

	test(
		"F-AGENT-06 edit a section's text; edit a page's text",
		{
			tag: ["@functional", "@llm", "@agent"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/21" },
		},
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT * 2);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const pagesBefore = await pageSnapshot(api, sourceDocument);
			const { children } = fixture.agent.outline;
			const edited = before.find((row) => row.name === children[1].name)!;
			const pageNo = children.at(-1)!.page_start;
			const pageRow = pagesBefore.find((row) => row.page_no === pageNo)!;
			const sectionPhrase = plainPhrase(edited.markdown);
			const pagePhrase = plainPhrase(pageRow.canonical_markdown);
			test.skip(!sectionPhrase || !pagePhrase, "no quotable phrase in the section or the page");
			const sectionEdit = `${sectionPhrase}, [test] section edit`;
			const pageEdit = `${pagePhrase} ([test] page edit)`;
			const sectionPrompt =
				`${PREFIX} agent ${stamp} In the section '${edited.title}', change the text '${sectionPhrase}' to ` +
				`'${sectionEdit}'. Change only that section.`;
			const pagePrompt =
				`Edit page ${pageNo} only (the page text, not any section): change '${pagePhrase}' to ` +
				`'${pageEdit}'. Do not change any section.`;
			let sessionId = "";
			try {
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, sectionPrompt);
				sessionId = await findSession(api, sourceDocument, sectionPrompt);
				let rows = await waitForTurn(api, sessionId, sectionPrompt, TURN_TIMEOUT);
				expect(toolRows(rows, ["edit_section_content"]).length).toBeGreaterThan(0);
				expect(await api.getValue("Source Section", edited.name, "markdown")).toContain(
					sectionEdit,
				);
				const article = page.getByRole("article");
				await sectionRow(page, edited.name).getByText(edited.title, { exact: true }).click();
				await expect(article).toContainText(sectionEdit);

				// Sent before the reload: a reloaded page opens a new chat, not this session.
				await sendPrompt(panel, pagePrompt);
				rows = await waitForTurn(api, sessionId, pagePrompt, TURN_TIMEOUT);
				expect(toolRows(rows, ["edit_page_content"]).length).toBeGreaterThan(0);
				expect(
					toolRows(rows, ["edit_section_content", "rebuild_section_from_pages"]),
				).toHaveLength(0);
				await waitForPropagation(api, sourceDocument);
				await page.reload();
				await sectionRow(page, edited.name).getByText(edited.title, { exact: true }).click();
				await expect(article).toContainText(sectionEdit);
				expect(await api.getValue("Source Page", pageRow.name, "canonical_markdown")).toContain(
					pageEdit,
				);
				await page.goto(`/wikify/import/${fixture.agent.import}/pages?page=${pageNo}`);
				await expect(page.getByText("([test] page edit)")).toBeVisible();

				// A page edit reaches a section only when that section alone owns the page.
				const ranges = await api.getList("Source Section", {
					filters: { source_document: sourceDocument },
					fields: ["name", "page_start", "page_end", "lft", "rgt"],
				});
				const covering = ranges.filter(
					(row) => row.page_start <= pageNo && row.page_end >= pageNo,
				);
				const owners = covering.filter(
					(row) => !covering.some((other) => other.lft > row.lft && other.rgt < row.rgt),
				);
				const changed = [edited.name, ...(owners.length === 1 ? [owners[0].name] : [])];
				const after = await sectionRows(api, sourceDocument);
				if (owners.length === 1)
					expect(after.find((row) => row.name === owners[0].name)!.markdown).toContain(pageEdit);
				const others = (rows: SectionRow[]) =>
					rows
						.filter((row) => !changed.includes(row.name))
						.map(({ title, markdown }) => ({ title, markdown }));
				expect(others(after)).toEqual(others(before));
			} finally {
				if (sessionId) await settle(api, sessionId);
				await waitForPropagation(api, sourceDocument);
				await restoreContent(api, sourceDocument, before, pagesBefore);
			}
			await expectPristine(api, sourceDocument, before);
			expect(await pageSnapshot(api, sourceDocument)).toEqual(pagesBefore);
		},
	);

	test(
		"F-AGENT-07 rebuild a section from its pages",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const pagesBefore = await pageSnapshot(api, sourceDocument);
			const candidates = fixture.agent.outline.children.flatMap((child) => {
				const section = before.find((row) => row.name === child.name)!;
				const sourcePage = pagesBefore.find((row) => row.page_no === section.page_start);
				const phrase = plainPhrase(section.markdown);
				return section.page_start === section.page_end &&
					phrase &&
					sourcePage?.canonical_markdown.includes(phrase)
					? [{ section, sourcePage, phrase }]
					: [];
			});
			test.skip(!candidates.length, "no one-page section quotes its page");
			const { section: target, sourcePage, phrase } = candidates[0];
			const marker = `[test] drift marker ${stamp}`;
			const prompt = `${PREFIX} agent ${stamp} Rebuild the section '${target.title}' from its pages.`;
			let sessionId = "";
			try {
				await setSectionMarkdown(api, sourceDocument, [
					{ name: target.name, markdown: `${target.markdown}\n\n${marker}` },
				]);
				const drifted = await sectionRows(api, sourceDocument);
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				const rebuilds = toolRows(rows, ["rebuild_section_from_pages"]);
				expect(rebuilds.map((row) => toolArgs(row).name)).toEqual([target.name]);
				// The rebuild adopts the whole page, and on a shared page the agent may then trim the
				// neighbours' text off (the tool's overlap warning tells it to). The tool row proves the page was adopted.
				expect(rebuilds[0].content).toContain(
					`(${sourcePage.canonical_markdown.trim().length} chars)`,
				);
				const after = await sectionRows(api, sourceDocument);
				const rebuilt = after.find((row) => row.name === target.name)!;
				expect(rebuilt.markdown).not.toContain(marker);
				expect(rebuilt.markdown).toContain(phrase);
				const others = (sections: SectionRow[]) =>
					sections.filter((row) => row.name !== target.name);
				expect(others(after)).toEqual(others(drifted));

				await page.reload();
				await sectionRow(page, target.name).getByText(target.title, { exact: true }).click();
				const article = page.getByRole("article");
				await expect(article).toContainText(phrase);
				await expect(article).not.toContainText(marker);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await restoreContent(api, sourceDocument, before, pagesBefore);
			}
			await expectPristine(api, sourceDocument, before);
		},
	);

	// The fixture's figure tags already point at the page image, so the test first breaks one by API.
	test(
		"F-AGENT-08 use the page image for a figure",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const pagesBefore = await pageSnapshot(api, sourceDocument);
			const found = await pageImageFigure(api, sourceDocument, before, fixture.agent.outline);
			test.skip(!found, "no top-level section embeds a page-image figure with a plain caption");
			const { owner, pageNo, caption, image } = found!;
			const target = pagesBefore.find((row) => row.page_no === pageNo)!;
			const placeholder = `/files/test-placeholder-${stamp}.png`;
			expect(target.canonical_markdown).toContain(`![${caption}](${image})`);
			expect(owner.markdown).toContain(`![${caption}](${image})`);
			const prompt = `${PREFIX} agent ${stamp} Use the page image for the figure "${caption}" on page ${pageNo}.`;
			let sessionId = "";
			try {
				await api.setValue("Source Page", target.name, {
					canonical_markdown: target.canonical_markdown.replace(
						`](${image})`,
						`](${placeholder})`,
					),
				});
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				const embeds = toolRows(rows, ["use_page_image"]);
				expect(embeds.length).toBeGreaterThan(0);
				expect(toolArgs(embeds.at(-1)!)).toMatchObject({ page_no: pageNo, caption });
				expect(await api.getValue("Source Page", target.name, "canonical_markdown")).toBe(
					target.canonical_markdown,
				);

				await waitForPropagation(api, sourceDocument);
				const ownerMarkdown: string = await api.getValue("Source Section", owner.name, "markdown");
				expect(ownerMarkdown).toContain(`![${caption}](${image})`);
				expect(ownerMarkdown).not.toContain(placeholder);

				const fileName = new RegExp(`${image.split("/").at(-1)!.replace(".", "\\.")}$`);
				await page.goto(`/wikify/import/${fixture.agent.import}/pages?page=${pageNo}`);
				await expect(page.getByRole("img", { name: caption, exact: true })).toHaveAttribute(
					"src",
					fileName,
				);
				await page.goto(`/wikify/import/${fixture.agent.import}/tree`);
				await sectionRow(page, owner.name).getByText(owner.title, { exact: true }).click();
				await expect(
					page.getByRole("article").getByRole("img", { name: caption, exact: true }),
				).toHaveAttribute("src", fileName);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await waitForPropagation(api, sourceDocument);
				await restoreContent(api, sourceDocument, before, pagesBefore);
			}
			await expectPristine(api, sourceDocument, before);
			expect(await pageSnapshot(api, sourceDocument)).toEqual(pagesBefore);
		},
	);

	test(
		"F-AGENT-09 crop a figure (no crop tool)",
		{
			tag: ["@functional", "@llm", "@agent"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/38" },
		},
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const before = await sectionRows(api, sourceDocument);
			const pagesBefore = await pageSnapshot(api, sourceDocument);
			const { pageNo, caption } = await findFigure(api, sourceDocument, { plainCaption: true });
			const prompt = `${PREFIX} agent ${Date.now()} Crop the figure "${caption}" on page ${pageNo} to its top half.`;
			let sessionId = "";
			try {
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				const reply = finalReply(rows);
				expect(reply.status).toBe("done");
				const writes = [
					"use_page_image",
					"edit_page_content",
					"edit_section_content",
					"reparse_page",
					"rebuild_section_from_pages",
					"reparse_document",
				];
				expect(toolRows(rows, writes)).toHaveLength(0);
				expect(await pageSnapshot(api, sourceDocument)).toEqual(pagesBefore);
				expect(await sectionRows(api, sourceDocument)).toEqual(before);
				// The wording is the bug (#38): the reply sends the user to the wiki editor, not the crop dialog.
				test.fail(); // known failure: #38. Remove test.fail() when the issue is closed.
				expect(reply.content).toMatch(/crop/i);
				expect(reply.content).toMatch(/\bPages\b/);
				expect(reply.content).toMatch(/click/i);
			} finally {
				if (sessionId) await settle(api, sessionId);
				await waitForPropagation(api, sourceDocument);
				await restoreContent(api, sourceDocument, before, pagesBefore);
			}
			await expectPristine(api, sourceDocument, before);
		},
	);

	// The page is usually shared by two sections, so the section side of the case is left to propagation (#21).
	test(
		"F-AGENT-10 reparse one page with the VLM, with an instruction",
		{ tag: ["@functional", "@llm", "@agent"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const before = await sectionRows(api, sourceDocument);
			const pagesBefore = await pageSnapshot(api, sourceDocument);
			const inlineCode = /`[^`\n]+`/;
			const found = pagesBefore.find((row) => inlineCode.test(row.canonical_markdown));
			test.skip(!found, "no page of this parse has inline code");
			const target = found!;
			const pageNo: number = target.page_no;
			const kept = plainPhrase(target.canonical_markdown.replace(/`[^`\n]+`/g, ""), 2);
			const reparseFields = [
				"canonical_source",
				"remediation_method",
				"remediation_markdown",
				"remediation_composite",
				"remediation_adopted",
				"remediation_notes",
			];
			const reparseBefore: Row = await api.getValue("Source Page", target.name, reparseFields);
			const meanBefore = await api.getValue("Source Document", sourceDocument, "canonical_mean");
			const prompt =
				`${PREFIX} agent ${stamp} Re-parse page ${pageNo} with the VLM, instruction: ` +
				`"write every inline code span as plain text, without backticks".`;
			let sessionId = "";
			try {
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);

				const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
				expect(finalReply(rows).status).toBe("done");
				const reparses = toolRows(rows, ["reparse_page"]);
				expect(reparses.length).toBeGreaterThan(0);
				const args = toolArgs(reparses.at(-1)!);
				expect(args).toMatchObject({ page_no: pageNo, method: "vlm" });
				expect(args.instruction).toMatch(/backtick/i);
				expect(reparses.at(-1)!.content).toContain(`Re-parsed page ${pageNo} via vlm`);

				const reparsed: string = await api.getValue(
					"Source Page",
					target.name,
					"canonical_markdown",
				);
				expect(reparsed).not.toBe(target.canonical_markdown);
				if (kept) expect(reparsed).toContain(kept);
				expect(reparsed).not.toMatch(inlineCode);

				await page.goto(`/wikify/import/${fixture.agent.import}/pages?page=${pageNo}`);
				if (kept) await expect(page.getByText(kept).first()).toBeVisible();
			} finally {
				if (sessionId) await settle(api, sessionId);
				await waitForPropagation(api, sourceDocument);
				await restoreContent(api, sourceDocument, before, pagesBefore);
				await api.setValue("Source Page", target.name, reparseBefore);
				await api.setValue("Source Document", sourceDocument, { canonical_mean: meanBefore });
			}
			await expectPristine(api, sourceDocument, before);
			expect(await pageSnapshot(api, sourceDocument)).toEqual(pagesBefore);
		},
	);

	test(
		"F-AGENT-11 cancel a long turn mid-way, then send a new prompt",
		{
			tag: ["@functional", "@llm", "@agent"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/24" },
		},
		async ({ page, api, fixture }) => {
			// Waits: queue (TURN_TIMEOUT) + stop (15 s) + follow-up or cleanup settle (TURN_TIMEOUT), so a timeout can't hide the bug.
			test.setTimeout(TURN_TIMEOUT * 2 + 120_000);
			const { sourceDocument } = fixture.agent;
			const sectionCount = (await sectionRows(api, sourceDocument)).length;
			const pageCount = (await pageSnapshot(api, sourceDocument)).length;
			const prompt =
				`${PREFIX} agent ${Date.now()} Read the sections strictly one at a time: call read_section for ONE section, wait for its result, ` +
				`then call it for the next one. Never put more than one tool call in a step. Read all ${sectionCount} sections, ` +
				`then call read_page for each of the ${pageCount} pages the same way, then list the sections without figures.`;
			const followUp = "How many sections does this document have? Answer with one number.";
			let sessionId = "";
			try {
				const panel = await openAssistant(page, fixture.agent.import);
				await sendPrompt(panel, prompt);
				sessionId = await findSession(api, sourceDocument, prompt);
				// A turn's rows are committed only when it ends, so progress is read from the live tool cards.
				const toolCards = panel.getByText(/^read_(section|page)$/);
				await expect
					.poll(() => toolCards.count(), { timeout: TURN_TIMEOUT })
					.toBeGreaterThanOrEqual(2);

				await iconButton(page, "lucide-square", panel).click();
				const toolsAtStop = await toolCards.count();
				await waitFor(
					() => api.getValue("Wikify Agent Session", sessionId, "is_running"),
					(running) => !running,
					{ timeout: 15_000, interval: 1_000, label: "is_running = 0 after Stop" },
				);
				const { messages } = await readSession(api, sessionId);
				// The step in flight when Stop landed may still record its one tool call.
				expect(messages.filter((row) => row.role === "tool").length).toBeLessThanOrEqual(
					toolsAtStop + 1,
				);

				await sendPrompt(panel, followUp);
				const rows = await waitForTurn(api, sessionId, followUp, TURN_TIMEOUT);
				const reply = finalReply(rows);
				expect(reply.status).toBe("done");
				expect(reply.content).toContain(String(sectionCount));
			} finally {
				if (sessionId) await settle(api, sessionId);
			}
		},
	);

	test(
		"F-AGENT-12 new session, rename it, archive it",
		{ tag: ["@functional", "@llm", "@agent", "@sanity"] },
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const firstPrompt = `${PREFIX} agent ${stamp} session list check`;
			const newTitle = `${PREFIX} agent ${stamp} renamed`;
			const panel = await openAssistant(page, fixture.agent.import);
			const header = panel.locator("header");

			await iconButton(page, "lucide-plus", header).click();
			await sendPrompt(panel, firstPrompt);
			const sessionId = await findSession(api, sourceDocument, firstPrompt);
			expect(await api.getValue("Wikify Agent Session", sessionId, ["title", "status"])).toEqual({
				title: firstPrompt,
				status: "Active",
			});

			await iconButton(page, "lucide-history", header).click();
			await expect(page.getByRole("menuitem", { name: firstPrompt })).toBeVisible();
			await page.keyboard.press("Escape");

			await iconButton(page, "lucide-pencil", header).click();
			const dialog = page.getByRole("dialog");
			await expect(dialog.getByText("Rename chat")).toBeVisible();
			await dialog.getByLabel("Chat title").fill(newTitle);
			await dialog.getByRole("button", { name: "Save" }).click();
			await expect(dialog).toBeHidden();
			expect(await api.getValue("Wikify Agent Session", sessionId, "title")).toBe(newTitle);

			await iconButton(page, "lucide-history", header).click();
			await expect(page.getByRole("menuitem", { name: newTitle })).toBeVisible();
			await expect(page.getByRole("menuitem", { name: firstPrompt })).toHaveCount(0);
			await page.keyboard.press("Escape");

			await iconButton(page, "lucide-archive", header).click();
			await expect
				.poll(() => api.getValue("Wikify Agent Session", sessionId, "status"))
				.toBe("Archived");

			const reopened = await openAssistant(page, fixture.agent.import);
			await iconButton(page, "lucide-history", reopened.locator("header")).click();
			await expect(page.getByRole("menu")).toBeVisible();
			await expect(page.getByRole("menuitem", { name: newTitle })).toHaveCount(0);
		},
	);

	test(
		"F-AGENT-13 session with 40+ messages remembers its first message",
		{
			tag: ["@functional", "@llm", "@agent"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/25" },
		},
		async ({ page, api, fixture }) => {
			test.setTimeout(TEST_TIMEOUT);
			const { sourceDocument } = fixture.agent;
			const stamp = Date.now();
			const codeWord = `ZEPHYR${stamp % 100000}`;
			const pageCount = (await pageSnapshot(api, sourceDocument)).length;
			const title = `${PREFIX} agent ${stamp} long history`;
			const { session_id: sessionId } = await api.call("wikify.api.agent.new_session", {
				scope: "document",
				source_document: sourceDocument,
			});
			createdSessions.add(sessionId);
			await api.call("wikify.api.agent.rename_session", { session_id: sessionId, title });

			// One earlier tool-heavy turn, built by REST: the code word lives only in the first user message.
			const calls = Array.from({ length: 42 }, (_, index) => ({
				id: `call_e2e_${stamp}_${index}`,
				name: "read_page",
				args: { page_no: (index % pageCount) + 1 },
			}));
			const history: Row[] = [
				{
					role: "user",
					content: `${PREFIX} agent ${stamp} Remember this code word for later in this chat: ${codeWord}.`,
				},
				{ role: "assistant", content: "Noted. I will keep it in mind." },
				{ role: "user", content: "Read every page of this document seven times." },
				{ role: "assistant", content: "", tool_calls: JSON.stringify(calls) },
				...calls.map((call) => ({
					role: "tool",
					tool_name: call.name,
					tool_call_id: call.id,
					content: `Page ${call.args.page_no} read.`,
				})),
				{ role: "assistant", content: `I read all ${pageCount} pages seven times.` },
			];
			for (const message of history) {
				await api.call("frappe.client.insert", {
					doc: { doctype: "Wikify Agent Message", session: sessionId, status: "done", ...message },
				});
			}
			const seeded = (await readSession(api, sessionId)).messages;
			expect(seeded.map((row) => row.role)).toEqual(history.map((row) => row.role));
			expect(seeded.filter((row) => row.role === "tool").length).toBeGreaterThanOrEqual(40);

			const panel = await openAssistant(page, fixture.agent.import);
			await iconButton(page, "lucide-history", panel.locator("header")).click();
			await page.getByRole("menuitem", { name: title }).click();
			await expect(panel.getByText(`I read all ${pageCount} pages seven times.`)).toBeVisible();
			const prompt =
				"What was the code word I gave you in my first message of this chat? Reply with only the code word.";
			await sendPrompt(panel, prompt);

			const rows = await waitForTurn(api, sessionId, prompt, TURN_TIMEOUT);
			const reply = finalReply(rows);
			expect(reply.status).toBe("done");
			expect(reply.content).toContain(codeWord);
		},
	);
});

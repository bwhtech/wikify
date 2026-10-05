import type { Page } from "@playwright/test";
import type { Api } from "../helpers/api";
import { deleteTestProjects } from "../helpers/cleanup";
import { FIXTURE_PDF, PREFIX } from "../helpers/env";
import { expect, test } from "../helpers/test";
import { waitFor, waitForValue } from "../helpers/wait";
import {
	createProject,
	PARSE_TIMEOUT,
	sectionRows,
	startImport,
	waitForImport,
} from "../helpers/wikify";

const AREA_PREFIX = `${PREFIX} parse ${Date.now()}`;
const ACTIVE = ["Queued", "Parsing", "Remediating"];
const RUNNING = ["Parsing", "Remediating"];
// One worker serves every queue on the bench, so a parse can sit Queued for many minutes first.
const QUEUE_WAIT = 1_200_000;
// Remediation runs every targeted page through the local Claude CLI, then re-sectionises and reclassifies.
const REMEDIATE_TIMEOUT = 1_200_000;

async function logEntries(api: Api, importName: string) {
	return api.getList("Import Log Entry", {
		filters: { import: importName },
		fields: ["name", "idx_seq", "stage", "message"],
		orderBy: "idx_seq asc",
	});
}

async function lastLogSeq(api: Api, importName: string): Promise<number> {
	const entries = await logEntries(api, importName);
	return entries.length ? entries[entries.length - 1].idx_seq : 0;
}

async function pageState(api: Api, sourceDocument: string) {
	return api.getList("Source Page", {
		filters: { source_document: sourceDocument },
		fields: [
			"page_no",
			"verdict",
			"remediation_method",
			"remediation_adopted",
			"canonical_source",
			"canonical_composite",
		],
		orderBy: "page_no asc",
	});
}

// Remediation log lines read "Page <n>  — <method> adopted|kept baseline (...)".
function remediatedPages(entries: { stage: string; message: string }[]): number[] {
	return entries
		.filter((entry) => entry.stage === "remediate")
		.map((entry) => entry.message.match(/^Page (\d+) /)?.[1])
		.filter((pageNo): pageNo is string => !!pageNo)
		.map(Number)
		.sort((a, b) => a - b);
}

test.describe("parse", () => {
	test.afterAll(async ({ api }) => {
		test.setTimeout(QUEUE_WAIT + PARSE_TIMEOUT);
		await deleteTestProjects(api, AREA_PREFIX);
	});

	test(
		"F-PARSE-01 stage label, Pages and Started follow a running parse",
		{
			tag: ["@functional", "@parse"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/48" },
		},
		async ({ page, api }) => {
			test.fail(); // known failure: #48. Remove test.fail() when the issue is closed.
			test.setTimeout(QUEUE_WAIT + 2 * PARSE_TIMEOUT);
			const projectName = `${AREA_PREFIX} watch`;
			const project = await createProject(api, projectName);
			const title = `${AREA_PREFIX} watch`;

			await page.goto(`/wikify/project/${project}`);
			await page.getByRole("button", { name: "New Document" }).first().click();
			const dialog = page.getByRole("dialog");
			const chooser = page.waitForEvent("filechooser");
			await dialog.getByRole("button", { name: "Choose PDFs" }).click();
			await (await chooser).setFiles(FIXTURE_PDF);
			await dialog.getByLabel("Title").fill(title);
			await dialog.getByRole("button", { name: "Start", exact: true }).click();

			await expect(page).toHaveURL(/\/wikify\/import\/IMP-[\d-]+$/);
			const importName = decodeURIComponent(page.url().split("/").pop()!);
			await expect(page.getByRole("heading", { name: title })).toBeVisible();
			const labelAtLoad = await api.getValue("Wikify Import", importName, "stage_label");

			await waitFor(
				() => api.getValue("Wikify Import", importName, ["status", "stage_label"]),
				(row) =>
					RUNNING.includes(row.status) && !!row.stage_label && row.stage_label !== labelAtLoad,
				{
					timeout: QUEUE_WAIT + PARSE_TIMEOUT,
					interval: 1_000,
					label: "stage label moved past its value at load",
				},
			);
			await expect
				.poll(
					async () => {
						const label = await api.getValue("Wikify Import", importName, "stage_label");
						return (await page.getByText(label, { exact: true }).isVisible())
							? "shown"
							: `"${label}" not shown`;
					},
					{ message: "header stage label follows the DB", timeout: 20_000 },
				)
				.toBe("shown");

			await page.getByRole("tab", { name: "Logs" }).click();
			expect(RUNNING).toContain(await api.getValue("Wikify Import", importName, "status"));
			const started = page
				.getByText("Started", { exact: true })
				.locator("xpath=following-sibling::p");
			await expect(started).not.toHaveText("—");

			const done = await waitForImport(api, importName, "Review");
			expect(done.stage_label).toBe("Parsed 6 pages");
			expect(
				await api.getValue("Wikify Import", importName, ["stage_progress", "page_count"]),
			).toEqual({
				stage_progress: 100,
				page_count: 6,
			});
			await expect(
				page.getByText("Pages", { exact: true }).locator("xpath=following-sibling::p"),
			).toHaveText("6");
		},
	);

	test(
		"F-PARSE-02 Logs tab streams lines during parse",
		{ tag: ["@functional", "@parse"] },
		async ({ page, api }) => {
			test.setTimeout(QUEUE_WAIT + 2 * PARSE_TIMEOUT);
			const project = await createProject(api, `${AREA_PREFIX} logs`);
			const title = `${AREA_PREFIX} logs`;
			const importName = await startImport(api, { title, project });

			await page.goto(`/wikify/import/${importName}`);
			await page.getByRole("tab", { name: "Logs" }).click();
			const panel = page.getByRole("tabpanel", { name: "Logs" });
			await expect(panel.getByText("Log", { exact: true })).toBeVisible();
			const seenAtLoad = new Set((await logEntries(api, importName)).map((entry) => entry.name));
			expect(ACTIVE).toContain(await api.getValue("Wikify Import", importName, "status"));

			const entries = await waitFor(
				() => logEntries(api, importName),
				(rows) =>
					rows.filter((row) => !seenAtLoad.has(row.name)).length >= 3 &&
					rows.some((row) => row.message.startsWith("Page ")),
				{
					timeout: QUEUE_WAIT + PARSE_TIMEOUT,
					interval: 2_000,
					label: "3 new log lines after page load",
				},
			);
			const fresh = entries.filter((entry) => !seenAtLoad.has(entry.name));
			for (const entry of fresh)
				await expect(panel.getByText(entry.message, { exact: true })).toBeVisible();
			await expect(panel.getByText(`Starting parse of ${title}`, { exact: true })).toBeVisible();
			await expect(panel.getByText(/^Page 1\/6 \(\w+\) — /)).toBeVisible();

			const text = await panel.innerText();
			expect(text.indexOf(fresh[fresh.length - 1].message), "newest line first").toBeLessThan(
				text.indexOf(`Starting parse of ${title}`),
			);

			await waitForValue(api, "Wikify Import", importName, "status", "Review", {
				timeout: PARSE_TIMEOUT,
				interval: 5_000,
			});
		},
	);

	test.describe("remediate and reclassify", () => {
		test.describe.configure({ mode: "serial", retries: 1 });

		let importName: string;
		let sourceDocument: string;
		let title: string;
		let parsedPages: Awaited<ReturnType<typeof pageState>>;

		test.beforeAll(async ({ api }) => {
			test.setTimeout(QUEUE_WAIT + PARSE_TIMEOUT + 60_000);
			title = `${AREA_PREFIX} remediate`;
			const project = await createProject(api, title);
			importName = await startImport(api, { title, project });
			sourceDocument = (await waitForImport(api, importName, "Review", QUEUE_WAIT + PARSE_TIMEOUT))
				.source_document;
			parsedPages = await pageState(api, sourceDocument);
			expect(parsedPages).toHaveLength(6);
		});

		async function remediateFromUi(page: Page, api: Api, menuItem: string): Promise<number> {
			const seqBefore = await lastLogSeq(api, importName);
			await page.goto(`/wikify/import/${importName}`);
			await expect(page.getByRole("heading", { name: title })).toBeVisible();
			await page.getByRole("button", { name: "Remediate" }).click();
			await page.getByRole("menuitem", { name: menuItem }).click();
			await waitForValue(api, "Wikify Import", importName, "status", "Remediating", {
				timeout: 30_000,
				interval: 1_000,
			});
			return seqBefore;
		}

		test(
			"F-PARSE-03 Remediate flagged keeps passed pages",
			{
				tag: ["@functional", "@llm", "@parse"],
				annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/31" },
			},
			async ({ page, api }) => {
				test.fail(); // known failure: #31. Remove test.fail() when the issue is closed.
				test.setTimeout(QUEUE_WAIT + REMEDIATE_TIMEOUT + 120_000);
				const before = await pageState(api, sourceDocument);
				const flagged = before.filter((row) => row.verdict !== "pass").map((row) => row.page_no);

				const seqBefore = await remediateFromUi(page, api, "Remediate flagged");
				const done = await waitForImport(
					api,
					importName,
					"Review",
					QUEUE_WAIT + REMEDIATE_TIMEOUT,
				);
				expect(done.error).toBeFalsy();
				expect(done.stage_label).toBe(`Remediated ${flagged.length} pages`);

				const fresh = (await logEntries(api, importName)).filter(
					(entry) => entry.idx_seq > seqBefore,
				);
				expect(
					fresh.some((entry) => entry.message.startsWith("Remediating flagged pages of")),
				).toBe(true);
				expect(remediatedPages(fresh)).toEqual(flagged);

				const canonical = (rows: typeof before) =>
					rows
						.filter((row) => before.find((old) => old.page_no === row.page_no)?.verdict === "pass")
						.map(({ page_no, canonical_source, canonical_composite }) => ({
							page_no,
							canonical_source,
							canonical_composite,
						}));
				expect(
					canonical(await pageState(api, sourceDocument)),
					"passed pages keep their canonical read",
				).toEqual(canonical(before));
			},
		);

		test(
			"F-PARSE-04 Remediate all pages",
			{ tag: ["@functional", "@llm", "@parse"] },
			async ({ page, api }) => {
				test.setTimeout(QUEUE_WAIT + REMEDIATE_TIMEOUT + 120_000);
				const seqBefore = await remediateFromUi(page, api, "Remediate all pages");
				const done = await waitForImport(
					api,
					importName,
					"Review",
					QUEUE_WAIT + REMEDIATE_TIMEOUT,
				);
				expect(done.error).toBeFalsy();
				expect(
					await api.getValue("Wikify Import", importName, [
						"status",
						"stage_label",
						"stage_progress",
						"page_count",
					]),
				).toEqual({
					status: "Review",
					stage_label: "Remediated 6 pages",
					stage_progress: 100,
					page_count: 6,
				});

				const fresh = (await logEntries(api, importName)).filter(
					(entry) => entry.idx_seq > seqBefore,
				);
				expect(fresh.some((entry) => entry.message.startsWith("Remediating all pages of"))).toBe(
					true,
				);
				expect(remediatedPages(fresh)).toEqual([1, 2, 3, 4, 5, 6]);

				const after = await pageState(api, sourceDocument);
				for (const row of after) {
					expect(row.remediation_method, `page ${row.page_no} remediated`).toBeTruthy();
					if (row.remediation_adopted)
						expect(row.canonical_source, `page ${row.page_no} adopted`).not.toBe("baseline");
				}
				const passedAfterParse = parsedPages
					.filter((row) => row.verdict === "pass")
					.map((row) => row.page_no);
				const passedNow = after.filter((row) => row.verdict === "pass").map((row) => row.page_no);
				expect(passedNow, "pages that passed after the parse pass again").toEqual(
					expect.arrayContaining(passedAfterParse),
				);
			},
		);

		test(
			"F-PARSE-05 Reclassify from the import page",
			{
				tag: ["@functional", "@llm", "@parse"],
				annotation: [
					{ type: "issue", description: "https://github.com/bwhtech/wikify/issues/33" },
					{ type: "issue", description: "https://github.com/bwhtech/wikify/issues/50" },
				],
			},
			async ({ page, api }) => {
				test.fail(); // known failure: #33, #50. Remove test.fail() when the issues are closed.
				test.setTimeout(QUEUE_WAIT + REMEDIATE_TIMEOUT);
				expect(await api.getValue("Wikify Import", importName, "status")).toBe("Review");
				const seqBefore = await lastLogSeq(api, importName);

				await page.goto(`/wikify/import/${importName}`);
				await expect(page.getByRole("heading", { name: title })).toBeVisible();
				const remediate = page.getByRole("button", { name: "Remediate" });
				await expect(remediate).toBeVisible();
				const headerControl = page.getByRole("button", { name: /Reclassify/ });
				if (!(await headerControl.isVisible())) await remediate.click();
				const control = headerControl.or(page.getByRole("menuitem", { name: /Reclassify/ }));
				await expect(control, "a Reclassify control on the import page").toBeVisible();
				await control.click();

				const entries = await waitFor(
					async () =>
						(await logEntries(api, importName)).filter((entry) => entry.idx_seq > seqBefore),
					(rows) =>
						rows.some((row) => row.stage === "classify" && row.message.startsWith("Done — ")),
					{
						timeout: QUEUE_WAIT + REMEDIATE_TIMEOUT / 2,
						interval: 3_000,
						label: "reclassify job done",
					},
				);
				const sections = await sectionRows(api, sourceDocument);
				const classified = entries
					.filter((entry) => entry.stage === "classify")
					.map((entry) => entry.message);
				for (const section of sections)
					expect(classified).toContain(`${section.title} → ${section.section_type}`);
				expect(await api.getValue("Wikify Import", importName, "status")).toBe("Review");
				await expect(
					page.getByRole("region", { name: /Notifications/ }).getByText(/classif/i),
				).toBeVisible();
			},
		);
	});

	test(
		"F-PARSE-06 parse a 398-page PDF to Review",
		{
			tag: ["@functional", "@parse"],
			annotation: { type: "issue", description: "https://github.com/bwhtech/wikify/issues/22" },
		},
		async () => {
			test.fixme(
				true,
				"needs a 400-page client PDF and over an hour of the only worker; the job is killed at its 3600 s RQ timeout (#22)",
			);
		},
	);
});

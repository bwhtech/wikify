import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import type { Api } from "../helpers/api";
import { deleteImport, deleteTestProjects } from "../helpers/cleanup";
import { PREFIX } from "../helpers/env";
import { writePdfs } from "../helpers/pdf";
import { expect, test } from "../helpers/test";
import { uploadFile } from "../helpers/upload";
import { waitFor, waitForValue } from "../helpers/wait";
import { createProject } from "../helpers/wikify";

const AREA_PREFIX = `${PREFIX} upload ${Date.now()}`;
const STARTED = ["Queued", "Parsing", "Remediating", "Review"];
const BULK_PROJECT = `${AREA_PREFIX} bulk`;
// One worker serves every queue on the bench, so an import can sit Queued for many minutes.
const QUEUE_WAIT = 1_200_000;

const strayImports: string[] = [];

function escapeRegExp(text: string): string {
	return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function projectImports(api: Api, project: string) {
	return api.getList("Wikify Import", {
		filters: { project },
		fields: ["name", "import_title", "status", "project"],
	});
}

async function dropFiles(
	page: Page,
	target: ReturnType<Page["getByText"]>,
	files: string[],
): Promise<void> {
	const dataTransfer = await page.evaluateHandle(
		(items) => {
			const transfer = new DataTransfer();
			for (const item of items) {
				const bytes = Uint8Array.from(atob(item.base64), (char) => char.charCodeAt(0));
				transfer.items.add(new File([bytes], item.name, { type: "application/pdf" }));
			}
			return transfer;
		},
		files.map((file) => ({
			name: path.basename(file),
			base64: fs.readFileSync(file).toString("base64"),
		})),
	);
	for (const type of ["dragenter", "dragover", "drop"])
		await target.dispatchEvent(type, { dataTransfer });
}

async function pickInDialog(page: Page, files: string | string[]): Promise<void> {
	const chooser = page.waitForEvent("filechooser");
	await page.getByRole("dialog").getByRole("button", { name: "Choose PDFs" }).click();
	await (await chooser).setFiles(files);
}

test.describe("upload", () => {
	test.afterAll(async ({ api }) => {
		// deleteImport waits for each queued import to finish parsing: 25 bulk PDFs at ~16 s each.
		test.setTimeout(1_500_000);
		for (const name of strayImports) await deleteImport(api, name);
		await deleteTestProjects(api, AREA_PREFIX);
	});

	test(
		"F-UP-06 create a project, upload into it",
		{ tag: ["@functional", "@upload", "@sanity"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(120_000);
			const projectName = `${AREA_PREFIX} project`;
			const [pdf] = writePdfs(testInfo.outputPath("pdfs"), [`${AREA_PREFIX} ui-01`]);

			await page.goto("/wikify/");
			await page.getByRole("button", { name: "New Project" }).first().click();
			const dialog = page.getByRole("dialog");
			await dialog.getByLabel("Project name").fill(projectName);
			await dialog.getByRole("button", { name: "Create" }).click();

			await expect(page).toHaveURL(/\/wikify\/project\/PRJ-[\d-]+$/);
			const project = decodeURIComponent(page.url().split("/").pop()!);
			await expect(page.getByText("No documents yet")).toBeVisible();
			await expect(page.getByRole("button", { name: "New Document" }).last()).toBeVisible();
			expect(
				await api.getValue("Wikify Project", project, ["project_name", "import_count"]),
			).toEqual({
				project_name: projectName,
				import_count: 0,
			});

			await page.getByRole("button", { name: "New Document" }).first().click();
			await pickInDialog(page, pdf);
			await page.getByRole("dialog").getByRole("button", { name: "Start", exact: true }).click();
			await expect(page).toHaveURL(/\/wikify\/import\/IMP-/);

			await page.goto("/wikify/");
			const card = page.getByRole("button", { name: new RegExp(escapeRegExp(projectName)) });
			await expect(card).toContainText("1 document");
			await card.click();
			await expect(page).toHaveURL(new RegExp(`/wikify/project/${project}$`));
			await expect(
				page.getByRole("button", { name: new RegExp(escapeRegExp(`${AREA_PREFIX} ui-01`)) }),
			).toBeVisible();
			await waitFor(
				() => api.getValue("Wikify Project", project, "import_count"),
				(count) => count === 1,
				{ label: "import_count = 1" },
			);
		},
	);

	test(
		"F-UP-01 upload 1 file through the New Document dialog",
		{ tag: ["@functional", "@upload"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(QUEUE_WAIT + 60_000);
			const projectName = `${AREA_PREFIX} dialog`;
			const project = await createProject(api, projectName);
			const title = `${AREA_PREFIX} up-01`;
			const [pdf] = writePdfs(testInfo.outputPath("pdfs"), [title]);

			await page.goto(`/wikify/project/${project}`);
			await page.getByRole("button", { name: "New Document" }).first().click();
			const dialog = page.getByRole("dialog");
			await expect(dialog.getByText("New Documents")).toBeVisible();
			await expect(dialog.getByLabel("Project")).toContainText(projectName);

			await pickInDialog(page, pdf);
			await expect(dialog.getByText("New Document", { exact: true })).toBeVisible();
			await expect(dialog.getByLabel("Title")).toHaveValue(title);
			await dialog.getByRole("button", { name: "Start", exact: true }).click();

			await expect(page).toHaveURL(/\/wikify\/import\/IMP-[\d-]+$/);
			const importName = decodeURIComponent(page.url().split("/").pop()!);
			await expect(page.getByRole("heading", { name: title })).toBeVisible();

			const rows = await api.getList("Wikify Import", {
				filters: { import_title: title },
				fields: ["name", "status", "project"],
			});
			expect(rows).toHaveLength(1);
			expect(rows[0]).toMatchObject({ name: importName, project });
			expect(STARTED).toContain(rows[0].status);
			await waitForValue(
				api,
				"Wikify Import",
				importName,
				"status",
				["Parsing", "Remediating", "Review"],
				{ timeout: QUEUE_WAIT },
			);
		},
	);

	test(
		"F-UP-02 drop 3 files on the document list",
		{ tag: ["@functional", "@upload", "@sanity"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(120_000);
			const project = await createProject(api, `${AREA_PREFIX} drop`);
			const titles = ["up-02", "up-03", "up-04"].map((suffix) => `${AREA_PREFIX} ${suffix}`);
			const files = writePdfs(testInfo.outputPath("pdfs"), titles);

			await page.goto(`/wikify/project/${project}`);
			const emptyState = page.getByText("No documents yet");
			await expect(emptyState).toBeVisible();
			await dropFiles(page, emptyState, files);

			await expect(page.getByText("3 documents queued")).toBeVisible();
			for (const title of titles) {
				await expect(
					page.getByRole("button", { name: new RegExp(escapeRegExp(title)) }),
				).toBeVisible();
			}
			const rows = await projectImports(api, project);
			expect(rows.map((row) => row.import_title).sort()).toEqual([...titles].sort());
			for (const row of rows) expect(STARTED).toContain(row.status);
		},
	);

	test(
		"F-UP-05 upload without choosing a project",
		{ tag: ["@negative", "@upload", "@sanity"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(120_000);
			const title = `${AREA_PREFIX} no-project`;
			const [pdf] = writePdfs(testInfo.outputPath("pdfs"), [title]);

			// The UI cannot leave the project empty (the dialog only opens from a project page), so call the API.
			const fileUrl = await uploadFile(api, pdf);
			const names = await api.call<string[]>("wikify.api.imports.start_imports", {
				files: [{ file_url: fileUrl, title }],
			});
			expect(names).toHaveLength(1);
			strayImports.push(...names);

			const imp = await api.getValue("Wikify Import", names[0], [
				"import_title",
				"project",
				"project_name",
				"status",
			]);
			const defaultProject = await api.getList("Wikify Project", {
				filters: { is_default: 1 },
				fields: ["name", "project_name"],
			});
			expect(defaultProject).toHaveLength(1);
			expect(imp).toMatchObject({
				import_title: title,
				project: defaultProject[0].name,
				project_name: "Uncategorized",
			});
			expect(STARTED).toContain(imp.status);

			await page.goto("/wikify/");
			await page.getByRole("button", { name: /^Uncategorized/ }).click();
			await expect(page).toHaveURL(new RegExp(`/wikify/project/${defaultProject[0].name}$`));
			await expect(
				page.getByRole("button", { name: new RegExp(escapeRegExp(title)) }),
			).toBeVisible();
		},
	);

	test(
		"F-UP-03 upload 25 files at once",
		{ tag: ["@functional", "@upload"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(180_000);
			const project = await createProject(api, BULK_PROJECT);
			const titles = Array.from(
				{ length: 25 },
				(_, index) => `${AREA_PREFIX} bulk-${String(index + 1).padStart(2, "0")}`,
			);
			const files = writePdfs(testInfo.outputPath("pdfs"), titles);

			await page.goto(`/wikify/project/${project}`);
			await page.getByRole("button", { name: "New Document" }).first().click();
			await pickInDialog(page, files);
			const dialog = page.getByRole("dialog");
			for (const title of titles)
				await expect(dialog.getByText(`${title}.pdf`, { exact: true })).toBeVisible();
			const start = dialog.getByRole("button", { name: "Start (25)" });
			await expect(start).toBeEnabled({ timeout: 60_000 });
			await start.click();

			await expect(dialog).toBeHidden({ timeout: 30_000 });
			await expect(page.getByText("25 documents queued")).toBeVisible();
			const rows = await projectImports(api, project);
			expect(rows).toHaveLength(25);
			expect(new Set(rows.map((row) => row.import_title)).size).toBe(25);
			expect(rows.map((row) => row.import_title).sort()).toEqual([...titles].sort());
			for (const row of rows) expect(STARTED).toContain(row.status);
		},
	);

	test(
		"F-UP-04 network drop during upload, reconnect, Retry",
		{ tag: ["@negative", "@upload"] },
		async ({ page, api }, testInfo) => {
			test.setTimeout(120_000);
			const project = await createProject(api, `${AREA_PREFIX} offline`);
			const titles = ["up-30", "up-31", "up-32"].map((suffix) => `${AREA_PREFIX} ${suffix}`);
			const files = writePdfs(testInfo.outputPath("pdfs"), titles);

			await page.goto(`/wikify/project/${project}`);
			await page.getByRole("button", { name: "New Document" }).first().click();
			const dialog = page.getByRole("dialog");
			await page.context().setOffline(true);
			await pickInDialog(page, files);

			const retry = dialog.getByRole("button", { name: "Retry" });
			await expect(retry).toHaveCount(3);
			await expect(dialog.getByText("Upload failed")).toHaveCount(3);
			await expect(dialog.getByRole("button", { name: "Start (0)" })).toBeDisabled();

			await page.context().setOffline(false);
			await retry.first().click();
			await expect(retry).toHaveCount(0);
			const start = dialog.getByRole("button", { name: "Start (3)" });
			await expect(start).toBeEnabled();

			await page.context().setOffline(true);
			await start.click();
			await expect(dialog.getByText("Failed to fetch")).toBeVisible();
			for (const title of titles)
				await expect(dialog.getByText(`${title}.pdf`, { exact: true })).toBeVisible();
			expect(await projectImports(api, project)).toHaveLength(0);

			await page.context().setOffline(false);
			await start.click();
			await expect(dialog).toBeHidden({ timeout: 30_000 });
			await expect(page.getByText("3 documents queued")).toBeVisible();
			const rows = await projectImports(api, project);
			expect(rows.map((row) => row.import_title).sort()).toEqual([...titles].sort());
		},
	);
});

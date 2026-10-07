import type { Api } from "./api";
import { waitFor } from "./wait";
import { renumberTree } from "./wikify";

const BUSY = ["Queued", "Parsing", "Remediating", "Generating Wiki"];

// The search-index rebuild that follows a parse can still hold row locks, so retry deadlocks.
async function tryDelete(api: Api, doctype: string, name: string, attempts = 4): Promise<void> {
	for (let attempt = 1; attempt <= attempts; attempt++) {
		try {
			await api.delete(doctype, name);
			return;
		} catch (error) {
			const message = (error as Error).message;
			if (attempt < attempts && /HTTP 508|Deadlock|modified/i.test(message)) {
				await new Promise((resolve) => setTimeout(resolve, 3_000 * attempt));
				continue;
			}
			console.warn(`cleanup: could not delete ${doctype} ${name}: ${message}`);
			return;
		}
	}
}

export async function deleteByPrefix(
	api: Api,
	doctype: string,
	titleField: string,
	prefix: string,
): Promise<string[]> {
	const rows = await api.getList(doctype, { filters: { [titleField]: ["like", `${prefix}%`] } });
	for (const row of rows) await tryDelete(api, doctype, row.name);
	return rows.map((row) => row.name);
}

async function deleteTree(api: Api, doctype: string, filters: Record<string, any>): Promise<void> {
	const rows = await api.getList(doctype, { filters, fields: ["name"], orderBy: "lft desc" });
	for (const row of rows) await tryDelete(api, doctype, row.name);
}

// Deleting the newest import reverts the naming series, so a still-queued job would parse the next
// import that reuses its name. Wait for the job to finish first.
export async function waitForIdle(
	api: Api,
	importName: string,
	timeout = 1_200_000,
): Promise<void> {
	await waitFor(
		() => api.getValue("Wikify Import", importName, "status"),
		(status) => !BUSY.includes(status),
		{ timeout, interval: 5_000, label: `${importName} idle` },
	).catch((error) => console.warn(`cleanup: ${error.message}`));
}

// A REST delete runs NestedSet's table-wide lft shift, which reorders other documents' trees
// (the [fixture] source among them). delete_section deletes raw and renumbers only this document.
async function deleteSections(api: Api, sourceDocument: string): Promise<void> {
	const filters = { source_document: sourceDocument, parent_source_section: ["is", "not set"] };
	const roots = await api.getList("Source Section", { filters });
	if (roots.length) await renumberTree(api, sourceDocument);
	for (const root of roots) {
		await api
			.call("wikify.api.sections.delete_section", { name: root.name })
			.catch((error) => console.warn(`cleanup: delete_section ${root.name}: ${error.message}`));
	}
	await deleteTree(api, "Source Section", { source_document: sourceDocument });
}

export async function deleteImport(api: Api, importName: string): Promise<void> {
	await waitForIdle(api, importName);
	const imp = await api.getValue("Wikify Import", importName, ["source_document"]);
	const sourceDocument = imp?.source_document;
	await deleteByFilter(api, "Import Log Entry", { import: importName });
	if (sourceDocument) {
		// Unlinked first: delete_section refuses a document whose import is Completed.
		await api.setValue("Wikify Import", importName, { source_document: null });
		await deleteByFilter(api, "Wikify Agent Session", { source_document: sourceDocument });
		await deleteSections(api, sourceDocument);
		await deleteByFilter(api, "Section Reference", { source_document: sourceDocument });
		await deleteByFilter(api, "Source Page", { source_document: sourceDocument });
		await api.setValue("Source Document", sourceDocument, {
			wiki_root_group: null,
			wiki_space: null,
			import: null,
		});
		await tryDelete(api, "Source Document", sourceDocument);
	}
	await tryDelete(api, "Wikify Import", importName);
}

async function deleteByFilter(
	api: Api,
	doctype: string,
	filters: Record<string, any>,
): Promise<void> {
	const rows = await api.getList(doctype, { filters });
	for (const row of rows) await tryDelete(api, doctype, row.name);
}

export async function deleteWikiSpace(api: Api, wikiSpace: string): Promise<void> {
	await tryDelete(api, "Wiki Space", wikiSpace);
}

// Runs once, in the teardown. Deleting imports mid-run reverts the IMP- naming series, so a new
// import reuses a deleted one's name and job id, and its parse job never runs.
export async function deleteTestProjects(api: Api, prefix: string): Promise<void> {
	const strays = await api.getList("Wikify Import", {
		filters: { import_title: ["like", `${prefix}%`] },
	});
	for (const imp of strays) await deleteImport(api, imp.name);
	const projects = await api.getList("Wikify Project", {
		filters: { project_name: ["like", `${prefix}%`] },
	});
	for (const project of projects) {
		const imports = await api.getList("Wikify Import", { filters: { project: project.name } });
		for (const imp of imports) await deleteImport(api, imp.name);
		await deleteByFilter(api, "Wikify Ask Session", { project: project.name });
		await deleteByFilter(api, "Wikify Agent Session", { project: project.name });
		await tryDelete(api, "Wikify Project", project.name);
	}
	const spaces = await api.getList("Wiki Space", {
		filters: { space_name: ["like", `${prefix}%`] },
	});
	for (const space of spaces) await deleteWikiSpace(api, space.name);
}

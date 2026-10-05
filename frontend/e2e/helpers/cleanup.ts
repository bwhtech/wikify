import type { Api } from "./api";

async function tryDelete(api: Api, doctype: string, name: string): Promise<void> {
	try {
		await api.delete(doctype, name);
	} catch (error) {
		console.warn(`cleanup: could not delete ${doctype} ${name}: ${(error as Error).message}`);
	}
}

export async function deleteByPrefix(api: Api, doctype: string, titleField: string, prefix: string): Promise<string[]> {
	const rows = await api.getList(doctype, { filters: { [titleField]: ["like", `${prefix}%`] } });
	for (const row of rows) await tryDelete(api, doctype, row.name);
	return rows.map((row) => row.name);
}

async function deleteTree(api: Api, doctype: string, filters: Record<string, any>): Promise<void> {
	const rows = await api.getList(doctype, { filters, fields: ["name"], orderBy: "lft desc" });
	for (const row of rows) await tryDelete(api, doctype, row.name);
}

export async function deleteImport(api: Api, importName: string): Promise<void> {
	const imp = await api.getValue("Wikify Import", importName, ["source_document"]);
	const sourceDocument = imp?.source_document;
	await deleteByFilter(api, "Import Log Entry", { import: importName });
	if (sourceDocument) {
		await deleteByFilter(api, "Wikify Agent Session", { source_document: sourceDocument });
		await deleteTree(api, "Source Section", { source_document: sourceDocument });
		await deleteByFilter(api, "Section Reference", { source_document: sourceDocument });
		await deleteByFilter(api, "Source Page", { source_document: sourceDocument });
		await api.setValue("Wikify Import", importName, { source_document: null });
		await api.setValue("Source Document", sourceDocument, { wiki_root_group: null, wiki_space: null, import: null });
		await tryDelete(api, "Source Document", sourceDocument);
	}
	await tryDelete(api, "Wikify Import", importName);
}

async function deleteByFilter(api: Api, doctype: string, filters: Record<string, any>): Promise<void> {
	const rows = await api.getList(doctype, { filters });
	for (const row of rows) await tryDelete(api, doctype, row.name);
}

export async function deleteWikiSpace(api: Api, wikiSpace: string): Promise<void> {
	await tryDelete(api, "Wiki Space", wikiSpace);
}

export async function deleteTestProjects(api: Api, prefix: string): Promise<void> {
	const projects = await api.getList("Wikify Project", { filters: { project_name: ["like", `${prefix}%`] } });
	for (const project of projects) {
		const imports = await api.getList("Wikify Import", { filters: { project: project.name } });
		for (const imp of imports) await deleteImport(api, imp.name);
		await deleteByFilter(api, "Wikify Ask Session", { project: project.name });
		await deleteByFilter(api, "Wikify Agent Session", { project: project.name });
		await tryDelete(api, "Wikify Project", project.name);
	}
	const spaces = await api.getList("Wiki Space", { filters: { space_name: ["like", `${prefix}%`] } });
	for (const space of spaces) await deleteWikiSpace(api, space.name);
}

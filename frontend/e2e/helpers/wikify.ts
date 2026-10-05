import path from "node:path";
import type { Api, Row } from "./api";
import { FIXTURE_PDF } from "./env";
import { uploadFile } from "./upload";
import { waitForValue } from "./wait";

export const PARSE_TIMEOUT = 600_000;

export type SectionRow = {
	name: string;
	title: string;
	parent_source_section: string | null;
	sort_order: number;
	level: number;
	is_group: number;
	include_in_wiki: number;
	section_type: string | null;
	wiki_document: string | null;
	page_start: number;
	page_end: number;
	lft: number;
	markdown: string;
};

export async function createProject(api: Api, projectName: string): Promise<string> {
	return api.call("wikify.api.projects.create_project", { project_name: projectName });
}

export async function findProject(api: Api, projectName: string): Promise<string | undefined> {
	const rows = await api.getList("Wikify Project", {
		filters: { project_name: projectName },
		limit: 1,
	});
	return rows[0]?.name;
}

export async function startImport(
	api: Api,
	{
		title,
		project,
		pdf = FIXTURE_PDF,
		fileName,
	}: { title: string; project: string; pdf?: string; fileName?: string },
): Promise<string> {
	const uniqueName =
		fileName ||
		`${path.basename(pdf, ".pdf")}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.pdf`;
	const fileUrl = await uploadFile(api, pdf, { fileName: uniqueName });
	return api.call("wikify.api.imports.start_import", { pdf_file_url: fileUrl, title, project });
}

export async function waitForImport(
	api: Api,
	importName: string,
	status: string | string[],
	timeout = PARSE_TIMEOUT,
) {
	await waitForValue(api, "Wikify Import", importName, "status", status, {
		timeout,
		interval: 5_000,
	});
	return api.getValue<Row>("Wikify Import", importName, [
		"status",
		"stage_label",
		"error",
		"source_document",
		"wiki_space",
	]);
}

export async function sectionRows(api: Api, sourceDocument: string): Promise<SectionRow[]> {
	return api.getList<SectionRow>("Source Section", {
		filters: { source_document: sourceDocument },
		fields: [
			"name",
			"title",
			"parent_source_section",
			"sort_order",
			"level",
			"is_group",
			"include_in_wiki",
			"section_type",
			"wiki_document",
			"page_start",
			"page_end",
			"lft",
			"markdown",
		],
		orderBy: "lft asc",
	});
}

export async function findSection(
	api: Api,
	sourceDocument: string,
	title: string,
): Promise<SectionRow> {
	const section = (await sectionRows(api, sourceDocument)).find((row) => row.title === title);
	if (!section) throw new Error(`No section titled "${title}" in ${sourceDocument}`);
	return section;
}

export async function pageRows(api: Api, sourceDocument: string): Promise<Row[]> {
	return api.getList("Source Page", {
		filters: { source_document: sourceDocument },
		fields: [
			"name",
			"page_no",
			"verdict",
			"composite",
			"canonical_composite",
			"canonical_source",
			"canonical_markdown",
		],
		orderBy: "page_no asc",
	});
}

export const FIXTURE_ROOT = "What Wikify Is";
export const FIXTURE_SECTIONS = [
	"Where your documents live",
	"How Wikify processes a PDF",
	"Reading the page review",
	"Sections and their labels",
	"Finding content across documents",
	"Generating the wiki",
	"The assistant",
	"Which AI service Wikify uses",
];

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

// move_section rebuilds lft/rgt for the whole document from sort_order.
export async function renumberTree(api: Api, sourceDocument: string): Promise<void> {
	const [firstRoot] = await api.getList("Source Section", {
		filters: { source_document: sourceDocument, parent_source_section: ["is", "not set"] },
		orderBy: "sort_order asc, lft asc",
		limit: 1,
	});
	if (!firstRoot) return;
	await api.call("wikify.api.sections.move_section", {
		name: firstRoot.name,
		new_parent: "",
		new_index: 0,
	});
}

// A REST save on a section with an empty old_parent runs NestedSet's move and scrambles lft, so
// renumber the tree afterwards.
export async function setSectionMarkdown(
	api: Api,
	sourceDocument: string,
	updates: { name: string; markdown: string }[],
): Promise<void> {
	if (!updates.length) return;
	for (const { name, markdown } of updates) {
		await api.setValue("Source Section", name, { markdown });
	}
	await renumberTree(api, sourceDocument);
}

const IMPORT_FIELDS = ["pdf", "page_count", "stage_label", "stage_progress", "started_at"];
const DOCUMENT_FIELDS = [
	"pdf",
	"page_count",
	"parser_used",
	"mean_score",
	"canonical_mean",
	"llm_cost",
	"status",
];
const PAGE_FIELDS = [
	"page_no",
	"kind",
	"image",
	"verdict",
	"composite",
	"text_recall",
	"extra_ratio",
	"table_score",
	"judge_score",
	"llm_cost",
	"notes",
	"baseline_markdown",
	"remediation_method",
	"remediation_adopted",
	"remediation_composite",
	"remediation_notes",
	"remediation_markdown",
	"canonical_source",
	"canonical_composite",
	"canonical_markdown",
];
const SECTION_FIELDS = [
	"title",
	"is_group",
	"section_type",
	"level",
	"sort_order",
	"page_start",
	"page_end",
	"include_in_wiki",
	"hierarchy_path",
	"markdown",
];
const REFERENCE_FIELDS = ["target_page", "anchor_text", "occurrences"];

function pick(row: Row, fields: string[]): Row {
	return Object.fromEntries(fields.map((field) => [field, row[field]]));
}

async function insertMany(api: Api, doctype: string, docs: Row[]): Promise<string[]> {
	const names: string[] = [];
	for (let start = 0; start < docs.length; start += 50) {
		const batch = docs.slice(start, start + 50).map((doc) => ({ doctype, ...doc }));
		names.push(...(await api.call<string[]>("frappe.client.insert_many", { docs: batch })));
	}
	return names;
}

export async function cloneImport(
	api: Api,
	sourceImport: string,
	{ title, project }: { title: string; project: string },
): Promise<{ import: string; sourceDocument: string }> {
	const original = await api.getValue<Row>("Wikify Import", sourceImport, [
		"source_document",
		...IMPORT_FIELDS,
	]);
	const from = original.source_document;
	const document = await api.getDoc("Source Document", from);
	const sourceDocument: string = (
		await api.call("frappe.client.insert", {
			doc: { doctype: "Source Document", title, project, ...pick(document, DOCUMENT_FIELDS) },
		})
	).name;

	const pages = await api.getList("Source Page", {
		filters: { source_document: from },
		fields: PAGE_FIELDS,
		orderBy: "page_no asc",
	});
	await insertMany(
		api,
		"Source Page",
		pages.map((page) => ({ ...page, source_document: sourceDocument })),
	);

	// A child needs its parent's new name, so each pass inserts the rows whose parent is already copied.
	let pending = await api.getList("Source Section", {
		filters: { source_document: from },
		fields: ["name", "parent_source_section", ...SECTION_FIELDS],
		orderBy: "lft asc",
	});
	const names = new Map<string, string>();
	while (pending.length) {
		const ready = pending.filter(
			(row) => !row.parent_source_section || names.has(row.parent_source_section),
		);
		if (!ready.length) throw new Error(`Orphan sections in ${from}: ${JSON.stringify(pending)}`);
		const inserted = await insertMany(
			api,
			"Source Section",
			ready.map((row) => ({
				...pick(row, SECTION_FIELDS),
				source_document: sourceDocument,
				parent_source_section: names.get(row.parent_source_section) ?? null,
			})),
		);
		ready.forEach((row, index) => names.set(row.name, inserted[index]));
		pending = pending.filter((row) => !names.has(row.name));
	}
	await renumberTree(api, sourceDocument);

	const references = await api.getList("Section Reference", {
		filters: { source_document: from },
		fields: ["from_section", "to_section", ...REFERENCE_FIELDS],
	});
	await insertMany(
		api,
		"Section Reference",
		references
			.filter((row) => names.has(row.from_section) && names.has(row.to_section))
			.map((row) => ({
				...pick(row, REFERENCE_FIELDS),
				from_section: names.get(row.from_section),
				to_section: names.get(row.to_section),
				source_document: sourceDocument,
			})),
	);

	const importName: string = (
		await api.call("frappe.client.insert", {
			doc: {
				doctype: "Wikify Import",
				import_title: title,
				project,
				status: "Review",
				source_document: sourceDocument,
				...pick(original, IMPORT_FIELDS),
			},
		})
	).name;
	await api.setValue("Source Document", sourceDocument, { import: importName });
	return { import: importName, sourceDocument };
}

export function escapeRegExp(text: string): string {
	return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Page edits are pushed into their sections by a queued job.
export async function propagationPending(api: Api, sourceDocument: string): Promise<boolean> {
	// RQ Job's virtual get_list needs an explicit order_by and a positive page length.
	const jobs = await api.call<{ arguments: string }[]>("frappe.client.get_list", {
		doctype: "RQ Job",
		filters: {
			queue: "long",
			status: ["in", ["queued", "started"]],
			job_name: "wikify.rag.events.propagate_dirty_pages",
		},
		fields: ["arguments"],
		order_by: "creation desc",
		limit_page_length: 1000,
	});
	return jobs.some((job) => JSON.parse(job.arguments).kwargs?.source_document === sourceDocument);
}

// Two parses of the same PDF differ in headings, captions and even tree shape, so specs pick
// sections by position and figures by search, never by title or page number.
export async function outline(api: Api, sourceDocument: string) {
	const rows = await sectionRows(api, sourceDocument);
	const topLevel = rows.filter((row) => !row.parent_source_section);
	const childrenOf = (parent: SectionRow) =>
		rows.filter((row) => row.parent_source_section === parent.name);
	const widest = topLevel.reduce<SectionRow | undefined>(
		(best, row) => (!best || childrenOf(row).length > childrenOf(best).length ? row : best),
		undefined,
	);
	const root = widest && childrenOf(widest).length >= 5 ? widest : undefined;
	const children = root ? childrenOf(root) : topLevel;
	if (children.length < 5) {
		const shape = rows.map((row) => [row.title, row.parent_source_section]);
		throw new Error(`Unexpected section tree for ${sourceDocument}: ${JSON.stringify(shape)}`);
	}
	return { rows, root, children };
}

export async function findFigure(
	api: Api,
	sourceDocument: string,
	{ fromPage = 1, toPage = Infinity, plainCaption = false } = {},
) {
	for (const page of await pageRows(api, sourceDocument)) {
		if (page.page_no < fromPage || page.page_no > toPage) continue;
		for (const [, caption, url] of (page.canonical_markdown || "").matchAll(
			/!\[([^\]]*)\]\(([^)]*)\)/g,
		)) {
			if (caption && !(plainCaption && caption.includes('"'))) {
				return { page: page.name as string, pageNo: page.page_no as number, caption, url };
			}
		}
	}
	throw new Error(`No figure on pages ${fromPage}-${toPage} of ${sourceDocument}`);
}

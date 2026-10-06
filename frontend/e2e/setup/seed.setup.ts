import { createHash } from "node:crypto";
import fs from "node:fs";
import { test as setup } from "@playwright/test";
import { Api } from "../helpers/api";
import { deleteImport } from "../helpers/cleanup";
import {
	FIXTURE_PDF,
	FIXTURE_PDF_OVERRIDDEN,
	FIXTURE_PDF_SHA256,
	FIXTURE_PREFIX,
	STATE_DIR,
} from "../helpers/env";
import {
	FIXTURES_FILE,
	type FixtureOutline,
	type Fixtures,
	type ImportFixture,
} from "../helpers/test";
import {
	cloneImport,
	createProject,
	findProject,
	outline,
	renumberTree,
	startImport,
	waitForImport,
	type SectionRow,
} from "../helpers/wikify";

const PROJECT_NAME = `${FIXTURE_PREFIX} e2e`;
const SOURCE_PROJECT_NAME = `${FIXTURE_PREFIX} source`;
const SOURCE_TITLE = `${FIXTURE_PREFIX} source`;
const IN_PROGRESS = ["Queued", "Parsing", "Remediating"];
const SOURCE_PARSE_TIMEOUT = 7_200_000;

function readPrevious(): Partial<Fixtures> {
	try {
		return JSON.parse(fs.readFileSync(FIXTURES_FILE, "utf8"));
	} catch {
		return {};
	}
}

function entry({ name, title, page_start, page_end }: SectionRow) {
	return { name, title, page_start, page_end };
}

function withoutNames({ root, children }: FixtureOutline): string {
	const strip = ({ name, ...rest }: { name: string }) => rest;
	return JSON.stringify({ root: root && strip(root), children: children.map(strip) });
}

async function intactOutline(
	api: Api,
	sourceDocument: string,
	recorded?: FixtureOutline,
): Promise<FixtureOutline | undefined> {
	try {
		const { rows, root, children } = await outline(api, sourceDocument);
		if (rows.some((row) => !row.include_in_wiki)) return undefined;
		const current = { root: root ? entry(root) : null, children: children.map(entry) };
		if (recorded && JSON.stringify(current) !== JSON.stringify(recorded)) return undefined;
		return current;
	} catch {
		return undefined;
	}
}

async function findOrCreateProject(api: Api, projectName: string): Promise<string> {
	return (await findProject(api, projectName)) || (await createProject(api, projectName));
}

async function seedSource(
	api: Api,
	pdfMd5: string,
	recorded?: ImportFixture,
): Promise<ImportFixture> {
	const project = await findOrCreateProject(api, SOURCE_PROJECT_NAME);
	const existing = await api.getList("Wikify Import", {
		filters: { import_title: SOURCE_TITLE, project },
		fields: ["name", "status", "source_document", "pdf"],
	});
	for (const imp of existing) {
		let { status, source_document: sourceDocument } = imp;
		const samePdf = (await api.getValue("File", { file_url: imp.pdf }, "content_hash")) === pdfMd5;
		if (samePdf && IN_PROGRESS.includes(status)) {
			const started = Date.now();
			({ status, source_document: sourceDocument } = await waitForImport(
				api,
				imp.name,
				["Review", "Failed"],
				SOURCE_PARSE_TIMEOUT,
			));
			console.log(`[seed] waited ${seconds(started)} for the running parse ${imp.name}`);
		}
		if (samePdf && status === "Review" && sourceDocument) {
			await renumberTree(api, sourceDocument);
			const outline = await intactOutline(
				api,
				sourceDocument,
				recorded?.import === imp.name ? recorded.outline : undefined,
			);
			if (outline) return { import: imp.name, sourceDocument, title: SOURCE_TITLE, outline };
		}
		await deleteImport(api, imp.name);
	}

	const started = Date.now();
	const name = await startImport(api, { title: SOURCE_TITLE, project });
	const { source_document: sourceDocument } = await waitForImport(
		api,
		name,
		"Review",
		SOURCE_PARSE_TIMEOUT,
	);
	console.log(`[seed] parsed ${FIXTURE_PDF} as ${name} in ${seconds(started)}`);
	const outline = await intactOutline(api, sourceDocument);
	if (!outline) throw new Error(`${name} parsed into an unusable section tree`);
	return { import: name, sourceDocument, title: SOURCE_TITLE, outline };
}

async function seedClone(
	api: Api,
	project: string,
	source: ImportFixture,
	title: string,
	recorded?: ImportFixture,
): Promise<ImportFixture> {
	const existing = await api.getList("Wikify Import", {
		filters: { import_title: title, project },
		fields: ["name", "status", "source_document"],
	});
	for (const imp of existing) {
		if (imp.status === "Review" && imp.source_document && imp.name === recorded?.import) {
			const outline = await intactOutline(api, imp.source_document, recorded.outline);
			if (outline)
				return { import: imp.name, sourceDocument: imp.source_document, title, outline };
		}
		await deleteImport(api, imp.name);
	}

	const started = Date.now();
	const clone = await cloneImport(api, source.import, { title, project });
	console.log(`[seed] cloned ${source.import} into ${clone.import} in ${seconds(started)}`);
	const outline = await intactOutline(api, clone.sourceDocument);
	if (!outline || withoutNames(outline) !== withoutNames(source.outline)) {
		throw new Error(`${clone.import} does not have the outline of ${source.import}`);
	}
	return { ...clone, title, outline };
}

function seconds(since: number): string {
	return `${Math.round((Date.now() - since) / 1000)}s`;
}

setup("seed the fixture project and imports", async () => {
	setup.setTimeout(SOURCE_PARSE_TIMEOUT + 900_000);
	const pdf = fs.readFileSync(FIXTURE_PDF);
	const digest = createHash("sha256").update(pdf).digest("hex");
	if (FIXTURE_PDF_OVERRIDDEN) {
		console.log(`[seed] E2E_FIXTURE_PDF is set: using ${FIXTURE_PDF}, sha256 check skipped`);
	} else if (digest !== FIXTURE_PDF_SHA256) {
		throw new Error(`${FIXTURE_PDF} has sha256 ${digest}, expected ${FIXTURE_PDF_SHA256}`);
	}
	const api = await Api.create();
	try {
		const previous = readPrevious();
		const source = await seedSource(
			api,
			createHash("md5").update(pdf).digest("hex"),
			previous.source,
		);
		const recorded = previous.source?.import === source.import ? previous : {};
		const project = await findOrCreateProject(api, PROJECT_NAME);
		const review = await seedClone(
			api,
			project,
			source,
			`${FIXTURE_PREFIX} review`,
			recorded.review,
		);
		const agent = await seedClone(api, project, source, `${FIXTURE_PREFIX} agent`, recorded.agent);
		const fixtures: Fixtures = { project, projectName: PROJECT_NAME, source, review, agent };
		fs.mkdirSync(STATE_DIR, { recursive: true });
		fs.writeFileSync(FIXTURES_FILE, JSON.stringify(fixtures, null, "\t"));
	} finally {
		await api.dispose();
	}
});

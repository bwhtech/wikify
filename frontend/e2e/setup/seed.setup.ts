import fs from "node:fs";
import { test as setup } from "@playwright/test";
import { Api } from "../helpers/api";
import { deleteImport } from "../helpers/cleanup";
import { FIXTURE_PREFIX, STATE_DIR } from "../helpers/env";
import { FIXTURES_FILE, type Fixtures, type ImportFixture } from "../helpers/test";
import {
	createProject,
	FIXTURE_ROOT,
	FIXTURE_SECTIONS,
	findProject,
	PARSE_TIMEOUT,
	sectionRows,
	startImport,
	waitForImport,
} from "../helpers/wikify";

const PROJECT_NAME = `${FIXTURE_PREFIX} e2e`;
const IN_PROGRESS = ["Queued", "Parsing", "Remediating"];

async function isPristine(api: Api, sourceDocument: string): Promise<boolean> {
	const rows = await sectionRows(api, sourceDocument);
	const root = rows.find((row) => row.title === FIXTURE_ROOT && !row.parent_source_section);
	const children = rows
		.filter((row) => root && row.parent_source_section === root.name)
		.map((row) => row.title);
	return (
		!!root &&
		rows.length === FIXTURE_SECTIONS.length + 1 &&
		JSON.stringify(children) === JSON.stringify(FIXTURE_SECTIONS) &&
		rows.every((row) => row.include_in_wiki === 1)
	);
}

async function seedImport(api: Api, project: string, title: string): Promise<ImportFixture> {
	const existing = await api.getList("Wikify Import", {
		filters: { import_title: title, project },
		fields: ["name", "status", "source_document"],
	});
	for (const imp of existing) {
		if (
			imp.status === "Review" &&
			imp.source_document &&
			(await isPristine(api, imp.source_document))
		) {
			return { import: imp.name, sourceDocument: imp.source_document, title };
		}
		if (IN_PROGRESS.includes(imp.status)) {
			const done = await waitForImport(api, imp.name, ["Review", "Failed"]);
			if (done.status === "Review" && (await isPristine(api, done.source_document))) {
				return { import: imp.name, sourceDocument: done.source_document, title };
			}
		}
		await deleteImport(api, imp.name);
	}
	const name = await startImport(api, { title, project });
	const done = await waitForImport(api, name, "Review");
	return { import: name, sourceDocument: done.source_document, title };
}

setup("seed the fixture project and imports", async () => {
	setup.setTimeout(2 * PARSE_TIMEOUT + 120_000);
	const api = await Api.create();
	try {
		const project =
			(await findProject(api, PROJECT_NAME)) || (await createProject(api, PROJECT_NAME));
		// One at a time: parallel inserts on a fresh site deadlock creating the IMP- naming series row.
		const review = await seedImport(api, project, `${FIXTURE_PREFIX} review`);
		const agent = await seedImport(api, project, `${FIXTURE_PREFIX} agent`);
		const fixtures: Fixtures = { project, projectName: PROJECT_NAME, review, agent };
		fs.mkdirSync(STATE_DIR, { recursive: true });
		fs.writeFileSync(FIXTURES_FILE, JSON.stringify(fixtures, null, "\t"));
	} finally {
		await api.dispose();
	}
});

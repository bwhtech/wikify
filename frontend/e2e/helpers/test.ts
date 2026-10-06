import fs from "node:fs";
import path from "node:path";
import { test as base } from "@playwright/test";
import { Api } from "./api";
import { STATE_DIR } from "./env";

export type ImportFixture = { import: string; sourceDocument: string; title: string };

export type Fixtures = {
	project: string;
	projectName: string;
	review: ImportFixture;
	agent: ImportFixture;
};

export const FIXTURES_FILE = path.join(STATE_DIR, "fixtures.json");

export const test = base.extend<{ fixture: Fixtures }, { api: Api }>({
	api: [
		async ({}, use) => {
			const api = await Api.create();
			await use(api);
			await api.dispose();
		},
		{ scope: "worker" },
	],
	fixture: async ({}, use) => {
		await use(JSON.parse(fs.readFileSync(FIXTURES_FILE, "utf8")));
	},
});

export { expect } from "@playwright/test";

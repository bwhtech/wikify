import { test as teardown } from "@playwright/test";
import { Api } from "../helpers/api";
import { deleteTestProjects } from "../helpers/cleanup";
import { PREFIX } from "../helpers/env";

teardown("delete leftover [test] records", async () => {
	teardown.setTimeout(2_700_000);
	const api = await Api.create();
	try {
		await deleteTestProjects(api, PREFIX);
	} finally {
		await api.dispose();
	}
});

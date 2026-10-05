import path from "node:path";
import { expect, test as setup } from "@playwright/test";
import { env, STATE_DIR } from "../helpers/env";

setup("log in as the e2e user", async ({ request }) => {
	const response = await request.post("/api/method/login", { data: { usr: env.user, pwd: env.password } });
	expect(response.ok(), await response.text()).toBeTruthy();
	await request.storageState({ path: path.join(STATE_DIR, "user.json") });
});

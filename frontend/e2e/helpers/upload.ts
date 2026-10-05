import fs from "node:fs";
import path from "node:path";
import type { Locator, Page } from "@playwright/test";
import { readJson, type Api } from "./api";

export async function uploadViaUi(page: Page, input: Locator | string, files: string | string[]): Promise<void> {
	const locator = typeof input === "string" ? page.locator(input) : input;
	await locator.setInputFiles(files);
}

export async function uploadFile(
	api: Api,
	filePath: string,
	{ doctype, docname, fileName, isPrivate = true }: { doctype?: string; docname?: string; fileName?: string; isPrivate?: boolean } = {},
): Promise<string> {
	const multipart: Record<string, any> = {
		file: { name: fileName || path.basename(filePath), mimeType: "application/pdf", buffer: fs.readFileSync(filePath) },
		is_private: isPrivate ? "1" : "0",
	};
	if (doctype) multipart.doctype = doctype;
	if (docname) multipart.docname = docname;
	const response = await api.context.post("/api/method/upload_file", { multipart });
	return (await readJson(response, `upload ${filePath}`)).message.file_url;
}

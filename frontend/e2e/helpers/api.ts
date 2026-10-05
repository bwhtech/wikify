import { request, type APIRequestContext, type APIResponse } from "@playwright/test";
import { env } from "./env";

export type Row = Record<string, any>;

export class Api {
	constructor(readonly context: APIRequestContext) {}

	static async create(): Promise<Api> {
		const context = await request.newContext({
			baseURL: env.baseURL,
			storageState: { cookies: [], origins: [] },
			extraHTTPHeaders: { Authorization: `token ${env.apiKey}:${env.apiSecret}` },
		});
		return new Api(context);
	}

	async call<T = any>(method: string, args: Row = {}): Promise<T> {
		const response = await this.context.post(`/api/method/${method}`, { data: args });
		return (await readJson(response, method)).message as T;
	}

	async getValue<T = any>(
		doctype: string,
		name: string | Row,
		fields: string | string[],
	): Promise<T> {
		const message = await this.call("frappe.client.get_value", {
			doctype,
			filters: name,
			fieldname: fields,
		});
		return (typeof fields === "string" ? message?.[fields] : message) as T;
	}

	async getList<T extends Row = Row>(
		doctype: string,
		{
			filters = {},
			fields = ["name"],
			orderBy = "creation desc",
			limit = 0,
		}: { filters?: Row | any[]; fields?: string[]; orderBy?: string; limit?: number } = {},
	): Promise<T[]> {
		return this.call("frappe.client.get_list", {
			doctype,
			filters,
			fields,
			order_by: orderBy,
			limit_page_length: limit,
		});
	}

	async getDoc<T extends Row = Row>(doctype: string, name: string): Promise<T> {
		const response = await this.context.get(
			`/api/resource/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`,
		);
		return (await readJson(response, `${doctype} ${name}`)).data as T;
	}

	async setValue(doctype: string, name: string, values: Row): Promise<Row> {
		const response = await this.context.put(
			`/api/resource/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`,
			{
				data: values,
			},
		);
		return (await readJson(response, `${doctype} ${name}`)).data;
	}

	async delete(doctype: string, name: string): Promise<void> {
		const response = await this.context.delete(
			`/api/resource/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`,
		);
		await readJson(response, `delete ${doctype} ${name}`);
	}

	async dispose(): Promise<void> {
		await this.context.dispose();
	}
}

export async function readJson(response: APIResponse, label: string): Promise<Row> {
	const text = await response.text();
	let body: Row = {};
	try {
		body = text ? JSON.parse(text) : {};
	} catch {
		body = { raw: text.slice(0, 500) };
	}
	if (!response.ok()) {
		throw new Error(`${label} failed with HTTP ${response.status()}: ${serverMessage(body)}`);
	}
	return body;
}

export function serverMessage(body: Row): string {
	if (body._server_messages) {
		try {
			return JSON.parse(body._server_messages)
				.map((message: string) => JSON.parse(message).message)
				.join("; ");
		} catch {
			return body._server_messages;
		}
	}
	return body.exception || body.exc_type || body.raw || JSON.stringify(body).slice(0, 500);
}

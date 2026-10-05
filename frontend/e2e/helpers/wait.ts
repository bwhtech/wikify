import type { Api } from "./api";

type Expected = string | number | string[] | ((value: any) => boolean);

function matches(value: any, expected: Expected): boolean {
	if (typeof expected === "function") return expected(value);
	if (Array.isArray(expected)) return expected.includes(value);
	return value === expected;
}

export async function waitForValue(
	api: Api,
	doctype: string,
	name: string,
	field: string,
	expected: Expected,
	{
		timeout = 300_000,
		interval = 3_000,
		errorField = "error",
	}: { timeout?: number; interval?: number; errorField?: string } = {},
): Promise<any> {
	const deadline = Date.now() + timeout;
	let last: any;
	while (Date.now() < deadline) {
		const row = await api.getValue(doctype, name, errorField ? [field, errorField] : [field]);
		last = row?.[field];
		if (matches(last, expected)) return last;
		if (row?.[errorField] && !matches(last, expected)) {
			throw new Error(
				`${doctype} ${name}: ${field} = ${JSON.stringify(last)}, ${errorField} = ${row[errorField]}`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, interval));
	}
	const error = errorField ? await api.getValue(doctype, name, errorField) : undefined;
	throw new Error(
		`${doctype} ${name}: ${field} never matched within ${timeout / 1000}s; last value ${JSON.stringify(last)}` +
			(error ? `, ${errorField} = ${error}` : ""),
	);
}

export async function waitFor<T>(
	read: () => Promise<T>,
	done: (value: T) => boolean,
	{
		timeout = 60_000,
		interval = 2_000,
		label = "condition",
	}: { timeout?: number; interval?: number; label?: string } = {},
): Promise<T> {
	const deadline = Date.now() + timeout;
	let last: T = await read();
	while (!done(last)) {
		if (Date.now() > deadline)
			throw new Error(
				`${label} not met within ${timeout / 1000}s; last value ${JSON.stringify(last)}`,
			);
		await new Promise((resolve) => setTimeout(resolve, interval));
		last = await read();
	}
	return last;
}

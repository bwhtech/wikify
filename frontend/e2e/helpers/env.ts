import path from "node:path";

function required(name: string): string {
	const value = process.env[name];
	if (!value)
		throw new Error(
			`${name} is not set. Copy e2e/.env.e2e.example to e2e/.env.e2e and fill it in.`,
		);
	return value;
}

export const env = {
	baseURL: required("E2E_BASE_URL"),
	user: required("E2E_USER"),
	password: required("E2E_PASSWORD"),
	apiKey: required("E2E_API_KEY"),
	apiSecret: required("E2E_API_SECRET"),
};

export const PREFIX = "[test]";
export const FIXTURE_PREFIX = "[fixture]";

export const STATE_DIR = path.resolve(import.meta.dirname, "../.state");
export const WHAT_IS_WIKIFY_PDF = path.resolve(
	import.meta.dirname,
	"../../../docs/what-is-wikify.pdf",
);
export const FIXTURE_PDF = WHAT_IS_WIKIFY_PDF;
export const FIXTURE_PDF_SHA256 =
	"ad23468830e90fc535623d6aab92d92bf64207bfb2178ab8e4a09c0b7d3352ad";

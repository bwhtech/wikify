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
export const FIXTURE_PDF_OVERRIDDEN = !!process.env.E2E_FIXTURE_PDF;
export const FIXTURE_PDF = FIXTURE_PDF_OVERRIDDEN
	? path.resolve(import.meta.dirname, "../..", process.env.E2E_FIXTURE_PDF!)
	: path.resolve(import.meta.dirname, "../fixtures/openstax-anatomy-ch24-25.pdf");
export const FIXTURE_PDF_SHA256 =
	"1ec2fc07da77527624ed607c3acbd7b2184ad1b575d07f0d1f157f5764e06c52";

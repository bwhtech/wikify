export const JEV_LOW_CONFIDENCE = 0.5;

export function jevPercent(score) {
	return Math.round(Number(score || 0) * 100);
}

export function jevTheme(score) {
	const n = Number(score || 0);
	if (n >= 0.85) return "green";
	if (n >= 0.7) return "orange";
	return "red";
}

export function jevDetail(page) {
	if (!page?.jev_detail) return {};
	if (typeof page.jev_detail === "string") {
		try {
			return JSON.parse(page.jev_detail);
		} catch {
			return {};
		}
	}
	return page.jev_detail;
}

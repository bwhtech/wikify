export const CATEGORIES = {
	document: { label: "Whole document", icon: "lucide-file-text" },
	tables: { label: "Tables", icon: "lucide-table-2" },
	text: { label: "Missing or extra text", icon: "lucide-text-search" },
	visuals: { label: "Images and diagrams", icon: "lucide-image" },
	structure: { label: "Structure", icon: "lucide-list-tree" },
	formatting: { label: "Formatting", icon: "lucide-code" },
};

export const TILE_CLASSES = {
	clean: "bg-surface-gray-3",
	improved: "bg-surface-blue-3",
	review: "bg-surface-amber-5",
	blocks: "bg-surface-red-5",
	reviewed: "bg-surface-green-5",
};

export const TILE_LEGEND = [
	{ state: "clean", label: "Clean" },
	{ state: "improved", label: "Improved automatically" },
	{ state: "review", label: "Needs review" },
	{ state: "blocks", label: "Blocks publish" },
	{ state: "reviewed", label: "Reviewed" },
];

export function severityBadge(issue) {
	return issue.severity === "blocks"
		? { label: "Blocks publish", theme: "red" }
		: { label: "Review", theme: "amber" };
}

export function issueLocation(issue) {
	const { page_no: start, page_end: end } = issue.target;
	if (!start) return "Document";
	return end ? `p. ${start}-${end}` : `p. ${start}`;
}

// Blocking items first, then page order; document items lead the queue.
export function sortIssues(issues) {
	return [...issues].sort(
		(a, b) =>
			(a.category === "document" ? 0 : 1) - (b.category === "document" ? 0 : 1) ||
			(a.severity === "blocks" ? 0 : 1) - (b.severity === "blocks" ? 0 : 1) ||
			(a.target.page_no || 0) - (b.target.page_no || 0)
	);
}

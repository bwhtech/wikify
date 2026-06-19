// Maps a Wikify Import status to a Badge theme (color = state, one place).
const THEMES = {
	Draft: "gray",
	Queued: "gray",
	Parsing: "blue",
	Review: "green",
	Remediating: "blue",
	"Tree Review": "green",
	Graphed: "green",
	"Generating Wiki": "blue",
	Completed: "green",
	Failed: "red",
};

export function statusTheme(status) {
	return THEMES[status] ?? "gray";
}

// Statuses where a background job is actively running (show the progress bar).
export function isActive(status) {
	return ["Queued", "Parsing", "Remediating", "Generating Wiki"].includes(status);
}

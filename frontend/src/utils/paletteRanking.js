import fuzzysort from "fuzzysort";

export const MIN_QUERY_LENGTH = 2;
export const CONTEXT_BIAS = 1.5;
const SHORT_THRESHOLD = 0.3;
const LONG_THRESHOLD = 0.5;

export function rankGroups(query, groups, contextProject = null) {
	const text = query.trim();
	return groups
		.map((group) => ({
			...group,
			items: text ? rankGroup(text, group, contextProject) : emptyQueryItems(group),
		}))
		.filter((group) => group.items.length);
}

export function flattenGroups(groups) {
	return groups.flatMap((group) => group.items);
}

function emptyQueryItems(group) {
	if (group.onlyWithQuery) return [];
	return group.emptyLimit ? group.items.slice(0, group.emptyLimit) : group.items;
}

function rankGroup(text, group, contextProject) {
	if (group.unranked) return group.items;
	const threshold = group.long ? LONG_THRESHOLD : SHORT_THRESHOLD;
	return fuzzysort
		.go(text, group.items, { key: "search", threshold })
		.map((result) => ({
			item: result.obj,
			score:
				result.score *
				(contextProject && result.obj.project === contextProject ? CONTEXT_BIAS : 1),
		}))
		.sort((a, b) => b.score - a.score)
		.map((ranked) => ranked.item);
}

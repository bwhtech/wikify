export const RECENTS_LIMIT = 5;

export function recentsKey(user) {
	return `wikify:recents:${user}`;
}

export function addRecent(list, entry) {
	if (!entry?.label) return list;
	const key = `${entry.kind}:${entry.name}`;
	return [{ ...entry, key }, ...list.filter((r) => r.key !== key)].slice(0, RECENTS_LIMIT);
}

export function readRecents(storage, user) {
	try {
		const list = JSON.parse(storage.getItem(recentsKey(user)));
		return Array.isArray(list) ? list : [];
	} catch {
		return [];
	}
}

export function writeRecents(storage, user, list) {
	try {
		storage.setItem(recentsKey(user), JSON.stringify(list));
	} catch {
		return;
	}
}

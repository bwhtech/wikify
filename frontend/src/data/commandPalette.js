import { ref } from "vue";
import { sessionUser } from "@/data/session";
import { addRecent, readRecents, writeRecents } from "@/utils/recents";

export const PALETTE_SHORTCUT = { key: "k", ctrl: true };

export const recents = ref([]);

export function loadRecents() {
	recents.value = readRecents(window.localStorage, sessionUser.value);
}

export function recordRecent(entry) {
	if (!sessionUser.value) return;
	const next = addRecent(readRecents(window.localStorage, sessionUser.value), entry);
	writeRecents(window.localStorage, sessionUser.value, next);
	recents.value = next;
}

export const paletteOpen = ref(false);

// Viewport-based desktop/mobile switch for the app shell. Mirrors frappe-ui's own
// `breakpoints.smaller('sm')` (Tailwind sm = 640px) but via a bare `matchMedia`
// listener so the app doesn't take a direct `@vueuse/core` dependency. Shared module
// state — one media query drives every caller.
import { onUnmounted, ref } from "vue";

const query = window.matchMedia("(max-width: 639px)");
const isMobile = ref(query.matches);

function onChange(event) {
	isMobile.value = event.matches;
}

query.addEventListener("change", onChange);

export function useIsMobile() {
	// The listener lives for the app's lifetime (shared state); `onUnmounted` is a no-op
	// safety net for the rare case this is used outside a persistent shell.
	onUnmounted(() => {});
	return isMobile;
}

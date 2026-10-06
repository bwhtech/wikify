import { ref } from "vue";

const STORAGE_KEY = "wikify-theme";
export const THEMES = [
	{ value: "light", label: "Light", icon: "lucide-sun" },
	{ value: "dark", label: "Dark", icon: "lucide-moon" },
	{ value: "system", label: "System", icon: "lucide-monitor" },
];
const currentTheme = ref("light"); // the stored mode: 'light' | 'dark' | 'system'
const resolvedTheme = ref("light"); // what's actually applied: 'light' | 'dark'

function resolve(theme) {
	if (theme === "system") {
		return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
	}
	return theme;
}

export function useTheme() {
	function setTheme(theme) {
		currentTheme.value = theme;
		resolvedTheme.value = resolve(theme);
		document.documentElement.setAttribute("data-theme", resolvedTheme.value);
		localStorage.setItem(STORAGE_KEY, theme);
	}

	function toggleTheme() {
		// Flip the *resolved* theme, not the stored mode — otherwise a 'system'
		// baseline that resolves to dark would need two clicks to reach light.
		setTheme(resolvedTheme.value === "dark" ? "light" : "dark");
	}

	function initializeTheme() {
		const stored = localStorage.getItem(STORAGE_KEY);
		setTheme(THEMES.some((t) => t.value === stored) ? stored : "system");
	}

	return { currentTheme, resolvedTheme, setTheme, toggleTheme, initializeTheme };
}

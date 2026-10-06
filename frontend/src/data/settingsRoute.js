import { computed, shallowRef } from "vue";
import { SETTINGS_TABS } from "@/data/settingsTabs";

export const SETTINGS_ROUTE_NAME = "Settings";
export const DEFAULT_SETTINGS_TAB = SETTINGS_TABS[0].value;

let router = null;
const backgroundRoute = shallowRef(null);

export function isSettingsTab(tab) {
	return SETTINGS_TABS.some((settingsTab) => settingsTab.value === tab);
}

export function attachSettingsRouter(instance) {
	router = instance;
	router.afterEach((to) => {
		if (to.name !== SETTINGS_ROUTE_NAME) backgroundRoute.value = to;
	});
}

function currentTab() {
	const location = router.currentRoute.value;
	if (location.name !== SETTINGS_ROUTE_NAME) return DEFAULT_SETTINGS_TAB;
	return location.params.tab || DEFAULT_SETTINGS_TAB;
}

export const settings = {
	get open() {
		return router.currentRoute.value.name === SETTINGS_ROUTE_NAME;
	},
	set open(shouldBeOpen) {
		if (shouldBeOpen === settings.open) return;
		if (shouldBeOpen) openSettings(currentTab());
		else closeSettings();
	},
	get tab() {
		return currentTab();
	},
	set tab(tab) {
		// Tabs write their model back on mount; this keeps a cold /settings/models link.
		if (!settings.open || tab === currentTab()) return;
		router.replace({ name: SETTINGS_ROUTE_NAME, params: { tab } });
	},
};

export const visibleRoute = computed(() =>
	settings.open && backgroundRoute.value ? backgroundRoute.value : router.currentRoute.value
);

export function openSettings(tab = DEFAULT_SETTINGS_TAB) {
	router.push({ name: SETTINGS_ROUTE_NAME, params: { tab } });
}

export function closeSettings() {
	router.push(backgroundRoute.value?.fullPath ?? "/");
}

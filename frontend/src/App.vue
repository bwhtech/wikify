<script setup>
import { provide, shallowReactive } from "vue";
import { START_LOCATION, routeLocationKey } from "vue-router";
import { FrappeUIProvider } from "frappe-ui";
import AppShell from "@/components/AppShell.vue";
import { session } from "@/data/session";
import { visibleRoute } from "@/data/settingsRoute";

// Pages behind the settings dialog keep seeing the route they were opened over.
const shellRoute = {};
for (const key in START_LOCATION) {
	Object.defineProperty(shellRoute, key, {
		get: () => visibleRoute.value[key],
		enumerable: true,
	});
}
provide(routeLocationKey, shallowReactive(shellRoute));
</script>

<template>
	<FrappeUIProvider>
		<AppShell v-if="session.isLoggedIn" />
	</FrappeUIProvider>
</template>

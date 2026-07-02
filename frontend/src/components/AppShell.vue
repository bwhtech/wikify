<script setup>
import { Button, Sidebar } from "frappe-ui";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useTheme } from "@/utils/useTheme";
import { session } from "@/data/session";
import AgentChatPanel from "@/components/AgentChatPanel.vue";
import AppSettingsDialog from "@/components/AppSettingsDialog.vue";

const route = useRoute();
const { resolvedTheme, toggleTheme, initializeTheme } = useTheme();

const settingsOpen = ref(false);

const header = computed(() => ({
	title: "Wikify",
	subtitle: session.user,
	menuItems: [
		{
			label: "Settings",
			icon: "lucide-settings",
			onClick: () => (settingsOpen.value = true),
		},
		{
			label: resolvedTheme.value === "dark" ? "Light mode" : "Dark mode",
			icon: resolvedTheme.value === "dark" ? "lucide-sun" : "lucide-moon",
			onClick: toggleTheme,
		},
		{
			label: "Log out",
			icon: "lucide-log-out",
			onClick: () => session.logout.submit(),
		},
	],
}));

// Projects owns every project/import screen, so highlight it for the whole subtree.
const PROJECT_ROUTES = ["Projects", "ProjectDetail", "ProjectSettings", "ImportDetail"];

const sections = computed(() => [
	{
		label: "",
		items: [
			{
				label: "Projects",
				icon: "lucide-folder",
				to: { name: "Projects" },
				isActive: PROJECT_ROUTES.includes(route.name),
			},
			{
				label: "Explore",
				icon: "lucide-shapes",
				to: { name: "Explore" },
				isActive: route.name === "Explore",
			},
		],
	},
]);

const collapsed = ref(localStorage.getItem("sidebar-collapsed") === "true");
watch(collapsed, (v) => localStorage.setItem("sidebar-collapsed", v));

// The agent panel + its floating button are mounted once here so they're available on
// every screen (slice 12).
const agentOpen = ref(false);

onMounted(initializeTheme);
</script>

<template>
	<div class="flex h-screen bg-surface-base text-ink-gray-9">
		<Sidebar v-model:collapsed="collapsed" :header="header" :sections="sections">
			<template #header-logo>
				<div class="flex h-full w-full items-center justify-center bg-surface-gray-3">
					<span class="lucide-book-open size-4 text-ink-gray-7" aria-hidden="true" />
				</div>
			</template>
		</Sidebar>

		<main class="flex flex-1 flex-col overflow-y-auto">
			<router-view />
		</main>

		<AppSettingsDialog v-model:open="settingsOpen" />

		<!-- Floating assistant button (every screen) + the slide-over panel. -->
		<Button
			v-show="!agentOpen"
			variant="solid"
			icon="lucide-sparkles"
			class="fixed bottom-5 right-5 z-30 !size-11 !rounded-full shadow-lg"
			tooltip="Ask the assistant"
			@click="agentOpen = true"
		/>
		<AgentChatPanel v-model:open="agentOpen" />
	</div>
</template>

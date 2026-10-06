<script setup>
import {
	BottomSheet,
	DesktopShell,
	MobileNav,
	MobileNavItem,
	MobileShell,
	Sidebar,
	SidebarCollapseToggle,
	SidebarHeader,
	SidebarItem,
} from "frappe-ui";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useTheme } from "@/utils/useTheme";
import { useIsMobile } from "@/composables/useMediaQuery";
import { session } from "@/data/session";
import AgentChatPanel from "@/components/AgentChatPanel.vue";
import AppSettingsDialog from "@/components/AppSettingsDialog.vue";

const route = useRoute();
const { resolvedTheme, toggleTheme, initializeTheme } = useTheme();
const isMobile = useIsMobile();

const BUG_REPORT_URL = "https://github.com/bwhtech/wikify/issues/new";

const settingsOpen = ref(false);
const mobileMenuOpen = ref(false);
const agentOpen = ref(false);

const menuItems = computed(() => [
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
		label: "Report a bug",
		icon: "lucide-bug",
		onClick: () => window.open(BUG_REPORT_URL, "_blank", "noopener"),
	},
	{
		label: "Log out",
		icon: "lucide-log-out",
		onClick: () => session.logout.submit(),
	},
]);

const header = computed(() => ({
	title: "Wikify",
	subtitle: session.user,
	menuItems: menuItems.value,
}));

// Projects owns every project/import screen, so highlight it for the whole subtree.
const PROJECT_ROUTES = ["Projects", "ProjectDetail", "ProjectSettings", "ImportDetail"];
const projectsActive = computed(() => PROJECT_ROUTES.includes(route.name));

// One list of destinations drives the desktop sidebar AND the mobile navigation, so a
// screen can never be reachable on one and unreachable on the other.
const destinations = computed(() => [
	{
		label: "Projects",
		icon: "lucide-folder",
		to: { name: "Projects" },
		isActive: projectsActive.value,
	},
	{
		label: "Explore",
		icon: "lucide-shapes",
		to: { name: "Explore" },
		isActive: route.name === "Explore",
	},
	{
		label: "Ask",
		icon: "lucide-message-circle-question",
		to: { name: "AskWiki" },
		isActive: route.name === "AskWiki",
	},
]);

const sections = computed(() => [{ label: "", items: destinations.value }]);

// Full-height, multi-pane routes own their own scroll (graph canvas, split review,
// tabbed import). Everything else scrolls as one page inside the shell's scroll area.
// Ask owns its scrolling: the transcript scrolls, the composer under it does not.
const FIXED_HEIGHT_ROUTES = ["Explore", "ProjectGraph", "ImportGraph", "AskWiki"];
const pageScroll = computed(() => !FIXED_HEIGHT_ROUTES.includes(route.name));

const collapsed = ref(localStorage.getItem("sidebar-collapsed") === "true");
watch(collapsed, (v) => localStorage.setItem("sidebar-collapsed", v));

function runMenuItem(item) {
	mobileMenuOpen.value = false;
	item.onClick();
}

onMounted(initializeTheme);
</script>

<template>
	<div class="h-screen bg-surface-base text-ink-gray-9">
		<!-- Mobile: fixed column with a bottom tab bar; the Sidebar's destinations
		     become tabs and the user menu moves into a bottom sheet. -->
		<MobileShell v-if="isMobile">
			<router-view />

			<template #nav>
				<MobileNav>
					<MobileNavItem
						label="Projects"
						icon="lucide-folder"
						:to="{ name: 'Projects' }"
						:active="projectsActive"
					/>
					<MobileNavItem
						label="Explore"
						icon="lucide-shapes"
						:to="{ name: 'Explore' }"
						:active="route.name === 'Explore'"
					/>
					<MobileNavItem
						label="Ask"
						icon="lucide-message-circle-question"
						:to="{ name: 'AskWiki' }"
						:active="route.name === 'AskWiki'"
					/>
					<MobileNavItem
						label="Assistant"
						icon="lucide-sparkles"
						:active="agentOpen"
						@click="agentOpen = true"
					/>
					<MobileNavItem
						label="Menu"
						icon="lucide-menu"
						:active="mobileMenuOpen"
						@click="mobileMenuOpen = true"
					/>
				</MobileNav>
			</template>
		</MobileShell>

		<!-- Desktop: icon-optional Sidebar + a scroll region that pins each page's
		     PageHeader above it. -->
		<DesktopShell v-else :scroll="pageScroll">
			<template #sidebar>
				<Sidebar v-model:collapsed="collapsed" class="border-r border-outline-gray-1">
					<div class="flex h-full flex-col p-2">
						<SidebarHeader
							:title="header.title"
							:subtitle="header.subtitle"
							:menu-items="header.menuItems"
						>
							<template #logo>
								<div
									class="flex h-full w-full items-center justify-center bg-surface-gray-3"
								>
									<span
										class="lucide-book-open size-4 text-ink-gray-7"
										aria-hidden="true"
									/>
								</div>
							</template>
						</SidebarHeader>
						<nav
							v-for="section in sections"
							:key="section.label"
							class="mt-2 flex flex-col gap-0.5"
						>
							<SidebarItem
								v-for="item in section.items"
								:key="item.label"
								:label="item.label"
								:icon="item.icon"
								:to="item.to"
								:active="item.isActive"
							/>
							<SidebarItem
								label="Assistant"
								icon="lucide-sparkles"
								:active="agentOpen"
								@click="agentOpen = !agentOpen"
							/>
						</nav>
						<div class="mt-auto">
							<SidebarCollapseToggle />
						</div>
					</div>
				</Sidebar>
				<AgentChatPanel v-model:open="agentOpen" docked />
			</template>

			<router-view />
		</DesktopShell>

		<AppSettingsDialog v-model:open="settingsOpen" />

		<!-- Mobile overflow menu (settings / theme / logout). -->
		<BottomSheet v-model:open="mobileMenuOpen" title="Wikify">
			<div class="flex flex-col px-2 pb-6">
				<button
					v-for="item in menuItems"
					:key="item.label"
					class="flex items-center gap-3 rounded-md px-3 py-3 text-left text-base text-ink-gray-8 active:bg-surface-gray-2"
					@click="runMenuItem(item)"
				>
					<span :class="[item.icon, 'size-5 text-ink-gray-6']" aria-hidden="true" />
					{{ item.label }}
				</button>
			</div>
		</BottomSheet>

		<AgentChatPanel v-if="isMobile" v-model:open="agentOpen" />
	</div>
</template>

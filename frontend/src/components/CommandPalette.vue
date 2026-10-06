<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Dialog, KeyboardShortcut, useCall, useList, useShortcut } from "frappe-ui";
import { agentContext } from "@/data/agentContext";
import { PALETTE_SHORTCUT, loadRecents, recents } from "@/data/commandPalette";
import { DESTINATIONS } from "@/data/navigation";
import { openSettings } from "@/data/settingsRoute";
import { SETTINGS_TABS } from "@/data/settingsTabs";
import { useRagAsk } from "@/composables/useRag";
import { THEMES, useTheme } from "@/utils/useTheme";
import { MIN_QUERY_LENGTH, flattenGroups, rankGroups } from "@/utils/paletteRanking";

const open = defineModel("open", { type: Boolean, default: false });
const props = defineProps({
	menuItems: { type: Array, default: () => [] },
});
const emit = defineEmits(["open-assistant"]);

const SEARCH_DEBOUNCE_MS = 300;
const PROJECT_LIMIT = 100;
const ENTITY_ROUTES = { project: "ProjectDetail", import: "ImportDetail" };
const ENTITY_ICONS = { project: "lucide-folder", import: "lucide-file-text" };

const route = useRoute();
const router = useRouter();
const { currentTheme, setTheme } = useTheme();
const { question, streaming, ask, newConversation } = useRagAsk();

const query = ref("");
const activeKey = ref(null);
const listbox = ref(null);

useShortcut({
	...PALETTE_SHORTCUT,
	description: "Open command palette",
	allowInInput: true,
	allowInDialog: true,
	preventDefault: false,
	handler(event) {
		if (event.defaultPrevented) return;
		if (!open.value && event.target.closest?.('[role="dialog"]')) return;
		event.preventDefault();
		open.value = !open.value;
	},
});

const projects = useList({
	doctype: "Wikify Project",
	fields: ["name", "project_name", "status"],
	orderBy: "modified desc",
	limit: PROJECT_LIMIT,
	immediate: false,
});

const search = useCall({
	url: "/api/v2/method/wikify.api.palette.search",
	immediate: false,
});

let projectsFetched = false;
watch(open, (isOpen) => {
	if (!isOpen) return;
	loadRecents();
	if (projectsFetched) return;
	projectsFetched = true;
	projects.fetch();
});

let searchTimer = null;
watch(query, (text) => {
	clearTimeout(searchTimer);
	if (text.trim().length < MIN_QUERY_LENGTH) return;
	searchTimer = setTimeout(() => search.submit({ query: text.trim() }), SEARCH_DEBOUNCE_MS);
});

const contextProject = computed(() =>
	route.params.name && agentContext.project ? agentContext.project : null
);

const projectLabels = computed(
	() => new Map((projects.data || []).map((p) => [p.name, p.project_name]))
);

function go(to) {
	return () => router.push(to);
}

function entityItem(kind, row) {
	return {
		key: `${kind}:${row.name}`,
		label: row.label,
		sub: row.sub,
		icon: ENTITY_ICONS[kind],
		project: kind === "project" ? row.name : row.project,
		search: [row.label, row.sub].filter(Boolean).join(" "),
		run: go({ name: ENTITY_ROUTES[kind], params: { name: row.name } }),
	};
}

function isCurrentPage(entry) {
	return route.name === ENTITY_ROUTES[entry.kind] && route.params.name === entry.name;
}

function action(key, label, icon, run, synonyms = "", extra = {}) {
	return { key: `action:${key}`, label, icon, run, search: `${label} ${synonyms}`, ...extra };
}

async function askWiki(text) {
	await router.push({ name: "AskWiki" });
	if (streaming.value) {
		question.value = text;
		return;
	}
	newConversation();
	question.value = text;
	ask();
}

const contextActions = computed(() => {
	const project = contextProject.value;
	if (!project) return [];
	const params = { name: project.name };
	return [
		action(
			"upload",
			`Upload PDF to ${project.label}`,
			"lucide-upload",
			go({ name: "ProjectDetail", params, query: { upload: 1 } }),
			"new import add document"
		),
		action(
			"project-settings",
			`${project.label} settings`,
			"lucide-sliders-horizontal",
			go({ name: "ProjectSettings", params }),
			"project configure context prompt model"
		),
		action(
			"project-graph",
			`${project.label} graph`,
			"lucide-waypoints",
			go({ name: "ProjectGraph", params }),
			"project graph map"
		),
	];
});

const actions = computed(() => [
	...contextActions.value,
	action(
		"new-project",
		"New project",
		"lucide-folder-plus",
		go({ name: "Projects", query: { new: 1 } }),
		"create project add"
	),
	action(
		"new-chat",
		"New conversation",
		"lucide-message-square-plus",
		() => router.push({ name: "AskWiki" }).then(() => !streaming.value && newConversation()),
		"ask chat question"
	),
	action(
		"assistant",
		"Open assistant",
		"lucide-sparkles",
		() => emit("open-assistant"),
		"agent ai help chat"
	),
	...SETTINGS_TABS.map((tab) =>
		action(
			`settings:${tab.value}`,
			`Settings: ${tab.label}`,
			tab.icon,
			() => openSettings(tab.value),
			"preferences configure"
		)
	),
	...THEMES.map((theme) =>
		action(
			`theme:${theme.value}`,
			`Theme: ${theme.label}`,
			theme.icon,
			() => setTheme(theme.value),
			"appearance mode color",
			{ checked: currentTheme.value === theme.value }
		)
	),
	...props.menuItems.map((item) =>
		action(`menu:${item.label}`, item.label, item.icon, item.onClick)
	),
]);

const sourceGroups = computed(() => {
	const text = query.value.trim();
	const serverRows = search.data || {};
	return [
		{
			id: "recent",
			title: "Recent",
			onlyWithoutQuery: true,
			items: recents.value
				.filter((entry) => !isCurrentPage(entry))
				.map((entry) => entityItem(entry.kind, entry)),
		},
		{
			id: "goto",
			title: "Go to",
			items: DESTINATIONS.map((d) => ({
				key: `goto:${d.route}`,
				label: d.label,
				icon: d.icon,
				search: d.label,
				run: go({ name: d.route }),
			})),
		},
		{
			id: "projects",
			title: "Projects",
			emptyLimit: 5,
			items: (projects.data || []).map((p) => ({
				...entityItem("project", { name: p.name, label: p.project_name }),
				trailingIcon: p.status === "Archived" ? "lucide-archive" : null,
				trailingLabel: p.status === "Archived" ? "Archived" : null,
			})),
		},
		{
			id: "imports",
			title: "Imports",
			long: true,
			onlyWithQuery: true,
			items: (serverRows.imports || []).map((row) =>
				entityItem("import", {
					name: row.name,
					label: row.title,
					sub: row.project_name,
					project: row.project,
				})
			),
		},
		{
			id: "conversations",
			title: "Conversations",
			long: true,
			onlyWithQuery: true,
			items: (serverRows.conversations || []).map((row) => ({
				key: `conversation:${row.name}`,
				label: row.title,
				sub: projectLabels.value.get(row.project) || "",
				icon: "lucide-messages-square",
				project: row.project,
				search: row.title,
				run: go({ name: "AskWiki", params: { conversationId: row.name } }),
			})),
		},
		{ id: "actions", title: "Actions", items: actions.value },
		{
			id: "ask",
			title: "Ask the wiki",
			unranked: true,
			onlyWithQuery: true,
			items: text
				? [
						{
							key: "ask",
							label: `Ask “${text}”`,
							icon: "lucide-message-circle-question",
							run: () => askWiki(text),
						},
				  ]
				: [],
		},
	].filter((group) => !(group.onlyWithoutQuery && text));
});

const groups = computed(() =>
	rankGroups(query.value, sourceGroups.value, contextProject.value?.name)
);
const flatItems = computed(() => flattenGroups(groups.value));
const activeItem = computed(
	() => flatItems.value.find((item) => item.key === activeKey.value) || flatItems.value[0]
);

watch(query, () => (activeKey.value = null));

function optionId(item) {
	return `palette-option-${item.key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function move(step) {
	const items = flatItems.value;
	if (!items.length) return;
	const index = items.indexOf(activeItem.value);
	activeKey.value = items[(index + step + items.length) % items.length].key;
	nextTick(() =>
		listbox.value
			?.querySelector(`#${optionId(activeItem.value)}`)
			?.scrollIntoView({ block: "nearest" })
	);
}

function select(item) {
	if (!item) return;
	open.value = false;
	item.run();
}

function reset() {
	query.value = "";
	activeKey.value = null;
	clearTimeout(searchTimer);
	search.reset();
}
</script>

<template>
	<Dialog v-model:open="open" bare size="xl" position="top" @after-leave="reset">
		<Dialog.Title class="sr-only">Command palette</Dialog.Title>
		<div class="flex items-center gap-3 border-b border-outline-gray-1 px-4">
			<span class="lucide-search size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
			<input
				v-model="query"
				type="text"
				role="combobox"
				aria-expanded="true"
				aria-autocomplete="list"
				aria-controls="palette-listbox"
				:aria-activedescendant="activeItem ? optionId(activeItem) : undefined"
				placeholder="Search projects, imports, actions… or ask a question"
				autocomplete="off"
				spellcheck="false"
				class="h-12 w-full border-none bg-transparent px-0 text-base text-ink-gray-8 placeholder-ink-gray-4 focus:ring-0"
				@keydown.down.prevent="move(1)"
				@keydown.up.prevent="move(-1)"
				@keydown.enter.prevent="select(activeItem)"
			/>
		</div>

		<div
			id="palette-listbox"
			ref="listbox"
			role="listbox"
			aria-label="Results"
			class="max-h-96 overflow-y-auto p-2"
		>
			<div
				v-for="group in groups"
				:key="group.id"
				role="group"
				:aria-labelledby="`palette-group-${group.id}`"
				class="mb-2 last:mb-0"
			>
				<div :id="`palette-group-${group.id}`" class="px-2 py-1.5 text-sm text-ink-gray-5">
					{{ group.title }}
				</div>
				<div
					v-for="item in group.items"
					:id="optionId(item)"
					:key="item.key"
					role="option"
					:aria-selected="item === activeItem"
					class="flex cursor-pointer items-center gap-3 rounded px-2 py-1.5"
					:class="item === activeItem ? 'bg-surface-gray-3' : ''"
					@mousemove="activeKey = item.key"
					@click="select(item)"
				>
					<span
						:class="[item.icon, 'size-4 shrink-0 text-ink-gray-6']"
						aria-hidden="true"
					/>
					<div class="min-w-0 flex-1">
						<div class="truncate text-base text-ink-gray-8">{{ item.label }}</div>
						<div v-if="item.sub" class="truncate text-sm text-ink-gray-5">
							{{ item.sub }}
						</div>
					</div>
					<span
						v-if="item.trailingIcon"
						:class="[item.trailingIcon, 'size-4 shrink-0 text-ink-gray-5']"
						role="img"
						:aria-label="item.trailingLabel"
					/>
					<span
						v-if="item.checked"
						class="lucide-check size-4 shrink-0 text-ink-gray-6"
						role="img"
						aria-label="Current"
					/>
				</div>
			</div>

			<div v-if="!groups.length" class="px-2 py-6 text-center text-base text-ink-gray-5">
				Nothing matches
			</div>
		</div>

		<div
			class="flex items-center gap-4 border-t border-outline-gray-1 px-4 py-2 text-sm text-ink-gray-5"
		>
			<span class="flex items-center gap-1.5">
				<KeyboardShortcut combo="ArrowUp" bg />
				<KeyboardShortcut combo="ArrowDown" bg />
				to move
			</span>
			<span class="flex items-center gap-1.5">
				<KeyboardShortcut combo="Enter" bg />
				to open
			</span>
			<span class="flex items-center gap-1.5">
				<KeyboardShortcut combo="Escape" bg />
				to close
			</span>
		</div>
	</Dialog>
</template>

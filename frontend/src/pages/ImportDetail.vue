<script setup>
import { computed, reactive, ref, watch, onMounted, onUnmounted } from "vue";
import { Badge, Button, Progress, Tabs, useDoc, useList } from "frappe-ui";
import { useSocket } from "@/socket";
import { statusTheme, isActive } from "@/utils/status";

const props = defineProps({
	name: { type: String, required: true },
	tab: { type: String, default: "overview" },
});

const imp = useDoc({ doctype: "Wikify Import", name: props.name });

const tabs = [
	{ label: "Overview", key: "overview" },
	{ label: "Pages", key: "pages" },
];
const activeTab = ref(props.tab === "pages" ? 1 : 0);

// Streaming log
const logs = useList({
	doctype: "Import Log Entry",
	fields: ["name", "idx_seq", "level", "stage", "message", "creation"],
	filters: { import: props.name },
	orderBy: "idx_seq asc",
	limit: 500,
});

// Per-page parse output — loads once the parse has produced a Source Document.
const pageFilters = reactive({ source_document: "__none__" });
const pages = useList({
	doctype: "Source Page",
	fields: ["name", "page_no", "kind", "image", "baseline_markdown"],
	filters: pageFilters,
	orderBy: "page_no asc",
	limit: 500,
});
watch(
	() => imp.doc?.source_document,
	(sd) => {
		if (sd) pageFilters.source_document = sd;
	},
	{ immediate: true },
);

const status = computed(() => imp.doc?.status);

// Realtime
const socket = useSocket();
function onProgress(payload) {
	if (payload.import !== props.name || !imp.doc) return;
	imp.doc.stage_progress = payload.percent;
	if (payload.status) imp.doc.status = payload.status;
	// Terminal transitions carry fields set server-side (source_document, error).
	if (payload.status === "Review" || payload.status === "Failed") {
		imp.reload();
	}
}
function onLog(payload) {
	if (payload.import !== props.name) return;
	if (Array.isArray(logs.data)) {
		logs.data.push({
			name: `live-${payload.idx_seq}`,
			idx_seq: payload.idx_seq,
			level: payload.level,
			stage: payload.stage,
			message: payload.message,
		});
	}
}
onMounted(() => {
	socket?.on("wikify_import_progress", onProgress);
	socket?.on("wikify_import_log", onLog);
});
onUnmounted(() => {
	socket?.off("wikify_import_progress", onProgress);
	socket?.off("wikify_import_log", onLog);
});

const levelColor = { info: "text-ink-gray-7", warn: "text-ink-amber-6", error: "text-ink-red-6" };
</script>

<template>
	<div class="flex h-full flex-col">
		<header
			class="sticky top-0 z-10 flex min-h-12 items-center gap-3 border-b border-outline-gray-1 bg-surface-base px-3 sm:px-5"
		>
			<Button variant="ghost" icon="lucide-arrow-left" :route="{ name: 'Imports' }" />
			<h1 class="truncate text-lg text-ink-gray-9">{{ imp.doc?.import_title || name }}</h1>
			<Badge v-if="status" :label="status" :theme="statusTheme(status)" variant="subtle" />
			<Progress
				v-if="isActive(status)"
				:value="imp.doc?.stage_progress || 0"
				size="sm"
				class="w-40"
			/>
			<span v-if="isActive(status)" class="text-sm text-ink-gray-5">{{
				imp.doc?.stage_label
			}}</span>
		</header>

		<Tabs v-model="activeTab" :tabs="tabs">
			<template #tab-panel="{ tab }">
				<!-- Overview -->
				<div v-if="tab.key === 'overview'" class="body-container pt-5 pb-40">
					<div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
						<div>
							<p class="text-sm text-ink-gray-5">Pages</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.page_count ?? "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Status</p>
							<p class="text-base text-ink-gray-8">{{ status }}</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Started</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.started_at || "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Completed</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.completed_at || "—" }}
							</p>
						</div>
					</div>

					<div
						v-if="imp.doc?.error"
						class="mt-5 rounded border border-outline-red-3 bg-surface-red-2 p-3"
					>
						<p class="mb-1 text-sm font-medium text-ink-red-6">Error</p>
						<pre class="overflow-x-auto whitespace-pre-wrap text-xs text-ink-red-6">{{
							imp.doc.error
						}}</pre>
					</div>

					<div class="mt-6">
						<p class="mb-2 text-sm text-ink-gray-5">Log</p>
						<div
							class="rounded-md border border-outline-gray-1 bg-surface-gray-1 p-3 font-mono text-xs"
						>
							<p v-if="!logs.data?.length" class="text-ink-gray-5">
								No log entries yet.
							</p>
							<div
								v-for="entry in logs.data"
								:key="entry.name"
								class="flex gap-2 py-0.5"
								:class="levelColor[entry.level] || 'text-ink-gray-7'"
							>
								<span class="shrink-0 text-ink-gray-4">[{{ entry.stage }}]</span>
								<span>{{ entry.message }}</span>
							</div>
						</div>
					</div>
				</div>

				<!-- Pages -->
				<div v-else-if="tab.key === 'pages'" class="body-container pt-5 pb-40">
					<p
						v-if="!pages.data?.length"
						class="py-10 text-center text-sm text-ink-gray-5"
					>
						No pages yet — parse still running or not started.
					</p>
					<div v-else class="space-y-6">
						<div
							v-for="page in pages.data"
							:key="page.name"
							class="rounded-md border border-outline-gray-1"
						>
							<div
								class="flex items-center gap-2 border-b border-outline-gray-1 px-3 py-2"
							>
								<span class="text-base text-ink-gray-8"
									>Page {{ page.page_no }}</span
								>
								<Badge
									:label="page.kind"
									:theme="page.kind === 'visual' ? 'orange' : 'gray'"
									variant="subtle"
									size="sm"
								/>
							</div>
							<div class="grid gap-4 p-3 md:grid-cols-2">
								<img
									v-if="page.image"
									:src="page.image"
									:alt="`Page ${page.page_no}`"
									class="w-full rounded border border-outline-gray-1"
								/>
								<pre
									class="max-h-[480px] overflow-auto whitespace-pre-wrap rounded border border-outline-gray-1 bg-surface-gray-1 p-3 text-xs text-ink-gray-8"
									>{{ page.baseline_markdown }}</pre
								>
							</div>
						</div>
					</div>
				</div>
			</template>
		</Tabs>
	</div>
</template>

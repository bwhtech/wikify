<script setup>
import { onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { Badge, Button, Progress, useList } from "frappe-ui";
import { useSocket } from "@/socket";
import { statusTheme, isActive } from "@/utils/status";

// Project-scoped imports list, embedded in ProjectDetail. The header + New Import
// button live in the parent; this owns the list body, its empty state, and realtime.
const props = defineProps({
	project: { type: String, required: true },
});
const emit = defineEmits(["new-import"]);

const router = useRouter();

const imports = useList({
	doctype: "Wikify Import",
	fields: ["name", "import_title", "status", "stage_progress", "page_count", "modified"],
	filters: { project: props.project },
	orderBy: "modified desc",
	limit: 50,
});

const socket = useSocket();
function onProgress(payload) {
	const row = imports.data?.find((r) => r.name === payload.import);
	if (row) {
		row.stage_progress = payload.percent;
		if (payload.status) row.status = payload.status;
	} else {
		// New import not in the page yet (just created) — pull it in.
		imports.reload();
	}
}

onMounted(() => socket?.on("wikify_import_progress", onProgress));
onUnmounted(() => socket?.off("wikify_import_progress", onProgress));

function openImport(name) {
	router.push({ name: "ImportDetail", params: { name } });
}

function fmtDate(d) {
	if (!d) return "";
	return new Date(d.replace(" ", "T")).toLocaleString();
}
</script>

<template>
	<div class="body-container pt-5 pb-40">
		<!-- Empty state -->
		<div
			v-if="!imports.loading && (imports.data?.length ?? 0) === 0"
			class="flex flex-col items-center justify-center gap-3 py-16 text-center"
		>
			<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
				<span class="lucide-inbox size-6" aria-hidden="true" />
			</div>
			<p class="text-base text-ink-gray-7">No documents yet</p>
			<p class="text-sm text-ink-gray-5">Upload a PDF to add your first document.</p>
			<Button
				variant="solid"
				theme="gray"
				icon-left="lucide-plus"
				label="New Document"
				class="mt-2"
				@click="emit('new-import')"
			/>
		</div>

		<!-- List -->
		<div v-else class="rounded-md border border-outline-gray-1">
			<div
				class="flex items-center gap-4 border-b border-outline-gray-1 px-4 py-2 text-sm text-ink-gray-5"
			>
				<span class="flex-1">Title</span>
				<span class="w-40 shrink-0">Status</span>
				<span class="w-16 shrink-0 text-right">Pages</span>
				<span class="w-44 shrink-0 text-right">Updated</span>
			</div>
			<button
				v-for="row in imports.data"
				:key="row.name"
				class="flex w-full items-center gap-4 border-b border-outline-gray-1 px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-gray-2"
				@click="openImport(row.name)"
			>
				<span class="flex-1 truncate text-base text-ink-gray-8">{{
					row.import_title
				}}</span>
				<span class="flex w-40 shrink-0 items-center gap-2">
					<Badge :label="row.status" :theme="statusTheme(row.status)" variant="subtle" />
					<Progress
						v-if="isActive(row.status)"
						:value="row.stage_progress || 0"
						size="sm"
						class="w-16"
					/>
				</span>
				<span class="w-16 shrink-0 text-right text-sm text-ink-gray-6">{{
					row.page_count || "—"
				}}</span>
				<span class="w-44 shrink-0 truncate text-right text-sm text-ink-gray-5">{{
					fmtDate(row.modified)
				}}</span>
			</button>
		</div>
	</div>
</template>

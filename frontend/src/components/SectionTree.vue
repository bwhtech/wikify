<script setup>
import { computed, ref, watch } from "vue";
import { Badge, Tree, useList } from "frappe-ui";
import { CodeEditor } from "frappe-ui/code-editor";
import { Splitpanes, Pane } from "splitpanes";
import "splitpanes/dist/splitpanes.css";

const props = defineProps({
	sourceDocument: { type: String, default: null },
	docTitle: { type: String, default: "Document" },
});

// Flat sections ordered by tree position (`lft`); the nesting is rebuilt client-side.
// useList with a computed filter refetches when the source document resolves (read-only
// here — drag-reorder lands in Slice 5).
const sections = useList({
	doctype: "Source Section",
	fields: [
		"name",
		"parent_source_section",
		"title",
		"is_group",
		"level",
		"section_type",
		"hierarchy_path",
		"page_start",
		"page_end",
		"sort_order",
		"markdown",
	],
	filters: computed(() => ({ source_document: props.sourceDocument || "__none__" })),
	orderBy: "lft asc",
	limit: 1000,
	auto: true,
});
defineExpose({ reload: () => sections.reload() });

const sectionCount = computed(() => (sections.data || []).length);

// Rebuild the parent→children nesting from the flat, lft-ordered rows.
const roots = computed(() => {
	const byName = {};
	for (const r of sections.data || []) byName[r.name] = { ...r, children: [] };
	const out = [];
	for (const r of sections.data || []) {
		const parent = r.parent_source_section;
		if (parent && byName[parent]) byName[parent].children.push(byName[r.name]);
		else out.push(byName[r.name]);
	}
	return out;
});

// The Tree component takes a single root; wrap the doc's top-level sections under a
// synthetic document node so the whole tree renders expanded.
const rootNode = computed(() => ({
	name: "__root__",
	title: props.docTitle,
	is_group: 1,
	children: roots.value,
}));

// Flat index for the detail pane lookup.
const byName = computed(() => {
	const out = {};
	for (const r of sections.data || []) out[r.name] = r;
	return out;
});

const selectedName = ref(null);
const selected = computed(() => byName.value[selectedName.value] || null);

// Default to the first top-level section once the tree loads.
watch(
	roots,
	(list) => {
		if (list.length && !byName.value[selectedName.value]) selectedName.value = list[0].name;
	},
	{ immediate: true },
);

function pageRange(node) {
	if (!node || node.name === "__root__") return null;
	return node.page_start === node.page_end
		? `p${node.page_start}`
		: `p${node.page_start}–${node.page_end}`;
}
</script>

<template>
	<div class="h-full">
		<p v-if="sections.loading && !sections.data" class="py-10 text-center text-sm text-ink-gray-5">
			Loading sections…
		</p>
		<p v-else-if="!sectionCount" class="py-10 text-center text-sm text-ink-gray-5">
			No sections yet — parse the document to build its tree.
		</p>

		<Splitpanes v-else class="h-full">
			<!-- Left: the section tree -->
			<Pane :size="40" :min-size="25" class="flex flex-col border-r border-outline-gray-1">
				<div
					class="flex items-center gap-2 border-b border-outline-gray-1 px-3 py-2 text-sm text-ink-gray-6"
				>
					<span class="font-medium text-ink-gray-8">Sections</span>
					<Badge :label="String(sectionCount)" theme="gray" variant="subtle" size="sm" />
				</div>
				<div class="flex-1 overflow-auto p-2">
					<Tree
						:node="rootNode"
						node-key="name"
						:options="{
							defaultCollapsed: false,
							rowHeight: '28px',
							indentWidth: '16px',
							showIndentationGuides: true,
						}"
					>
						<template #label="{ node }">
							<button
								class="flex min-w-0 flex-1 items-center gap-2 rounded px-1.5 py-0.5 text-left hover:bg-surface-gray-2"
								:class="
									selectedName === node.name && node.name !== '__root__'
										? 'bg-surface-gray-3'
										: ''
								"
								@click.stop="node.name !== '__root__' && (selectedName = node.name)"
							>
								<span
									class="truncate text-sm"
									:class="
										node.name === '__root__'
											? 'font-medium text-ink-gray-9'
											: 'text-ink-gray-8'
									"
									>{{ node.title }}</span
								>
								<Badge
									v-if="node.section_type"
									:label="node.section_type"
									theme="blue"
									variant="subtle"
									size="sm"
								/>
								<span
									v-if="pageRange(node)"
									class="ml-auto shrink-0 text-xs tabular-nums text-ink-gray-4"
									>{{ pageRange(node) }}</span
								>
							</button>
						</template>
					</Tree>
				</div>
			</Pane>

			<!-- Right: selected section body -->
			<Pane :size="60" class="flex flex-col">
				<template v-if="selected">
					<div class="border-b border-outline-gray-1 px-4 py-3">
						<div class="flex items-center gap-2">
							<span class="text-base font-medium text-ink-gray-9">{{
								selected.title
							}}</span>
							<Badge
								:label="`Level ${selected.level}`"
								theme="gray"
								variant="subtle"
								size="sm"
							/>
							<Badge
								v-if="pageRange(selected)"
								:label="pageRange(selected)"
								theme="gray"
								variant="subtle"
								size="sm"
							/>
						</div>
						<p class="mt-1 truncate text-xs text-ink-gray-5">
							{{ selected.hierarchy_path }}
						</p>
					</div>
					<div class="min-h-0 flex-1 overflow-auto p-3">
						<CodeEditor
							:model-value="selected.markdown || ''"
							language="markdown"
							variant="outline"
							:disabled="true"
							class="min-h-0 flex-1"
						/>
					</div>
				</template>
				<p v-else class="py-10 text-center text-sm text-ink-gray-5">
					Select a section to view its content.
				</p>
			</Pane>
		</Splitpanes>
	</div>
</template>

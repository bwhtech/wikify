<script setup>
/**
 * Project-scope graph route (0.5 Slice 28): /project/:name/graph — every document in
 * the project with its section subgraph; clusters emerge per document, Section Type
 * colors line up across them. Clicking a section deep-links into its import's Tree
 * tab; clicking a document node opens that import.
 */
import { useRouter } from "vue-router";
import { Button, useDoc, useList } from "frappe-ui";
import GraphView from "@/components/GraphView.vue";

const props = defineProps({
	name: { type: String, required: true },
});

const router = useRouter();
const project = useDoc({ doctype: "Wikify Project", name: props.name });

// Section/document node ids are Source Document-side; navigation needs the Import.
const imports = useList({
	doctype: "Wikify Import",
	fields: ["name", "source_document"],
	filters: { project: props.name },
	limit: 500,
	auto: true,
});

function importFor(sourceDocument) {
	return (imports.data || []).find((i) => i.source_document === sourceDocument)?.name;
}

function onSelect(node) {
	const imp = importFor(node.kind === "section" ? node.doc : node.id);
	if (!imp) return;
	if (node.kind === "section") {
		router.push({
			name: "ImportDetail",
			params: { name: imp, tab: "tree" },
			query: { section: node.id },
		});
	} else {
		router.push({ name: "ImportDetail", params: { name: imp } });
	}
}
</script>

<template>
	<div class="flex h-screen flex-col">
		<header
			class="sticky top-0 z-10 flex min-h-12 items-center gap-3 border-b border-outline-gray-1 bg-surface-base px-3 sm:px-5"
		>
			<Button
				variant="ghost"
				icon="lucide-arrow-left"
				:route="{ name: 'ProjectDetail', params: { name: props.name } }"
			/>
			<RouterLink
				:to="{ name: 'ProjectDetail', params: { name: props.name } }"
				class="truncate text-base text-ink-gray-5 hover:text-ink-gray-7"
				>{{ project.doc?.project_name || props.name }}
				<span class="text-ink-gray-4" aria-hidden="true">/</span></RouterLink
			>
			<h1 class="text-md text-ink-gray-9">Graph</h1>
		</header>

		<div class="min-h-0 flex-1">
			<GraphView
				:url="'/api/v2/method/wikify.api.graph.get_project_graph'"
				:params="{ project: props.name }"
				@select="onSelect"
			/>
		</div>
	</div>
</template>

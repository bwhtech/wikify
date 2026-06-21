<script setup>
// Rendered-markdown view shared by the page Preview tab and the Tree section body.
// Renders markdown with `marked` (prose-styled) and post-processes ```mermaid fences
// into SVG diagrams via the wiki app's mermaid loader (see utils/mermaid).
import { computed, ref, watch, nextTick, onMounted } from "vue";
import { marked } from "marked";
import { renderMermaidIn } from "@/utils/mermaid";

const props = defineProps({
	content: { type: String, default: "" },
});

const container = ref(null);
const html = computed(() => marked.parse(props.content || "", { async: false }));

async function renderDiagrams() {
	await nextTick();
	renderMermaidIn(container.value);
}

onMounted(renderDiagrams);
watch(() => props.content, renderDiagrams);
</script>

<template>
	<div ref="container" class="prose prose-sm dark:prose-invert max-w-none" v-html="html" />
</template>

<style>
.mermaid-figure {
	display: flex;
	justify-content: center;
	overflow-x: auto;
	margin: 1rem 0;
}
.mermaid-figure svg {
	max-width: 100%;
	height: auto;
}
</style>

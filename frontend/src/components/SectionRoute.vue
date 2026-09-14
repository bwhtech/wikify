<script setup>
import { computed } from "vue";

const props = defineProps({
	section: { type: Object, required: true },
});

const route = computed(() => props.section.hierarchy_path || props.section.title || "");
const segments = computed(() => route.value.split(" > "));
const ancestors = computed(() => segments.value.slice(0, -1).join(" > "));
const leaf = computed(() => segments.value.at(-1));

// hierarchy_path already ends with the section's own title, so a second line
// repeating it reads as a rendering bug — only show it when it differs.
const subtitle = computed(() => (props.section.title === leaf.value ? "" : props.section.title));
</script>

<template>
	<div class="min-w-0 flex-1">
		<!-- Narrow screens put the ancestors on their own truncated line so the leaf —
		     the only part that differs between sibling rows — is never the bit cut off. -->
		<p class="text-base break-words">
			<span
				v-if="ancestors"
				class="block truncate text-xs text-ink-gray-5 sm:inline sm:text-base"
			>
				{{ ancestors }}<span class="hidden sm:inline"> &gt; </span>
			</span>
			<span class="text-ink-gray-8">{{ leaf }}</span>
		</p>
		<p v-if="subtitle" class="hidden truncate text-xs text-ink-gray-5 sm:block">
			{{ subtitle }}
		</p>
	</div>
</template>

<script setup>
import { Badge, Select } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";

const props = defineProps({
	suggestion: { type: Object, required: true },
	selected: { type: Boolean, default: false },
	disabled: { type: Boolean, default: false },
});
const value = defineModel("value", { type: String, default: "" });
const emit = defineEmits(["select"]);

function pick() {
	if (!props.disabled) emit("select");
}
</script>

<template>
	<div
		class="rounded-lg border transition-colors"
		:class="
			selected
				? 'border-outline-gray-4 bg-surface-elevation-1 shadow-sm'
				: 'border-outline-gray-2'
		"
	>
		<button
			role="radio"
			:aria-checked="selected"
			:disabled="disabled"
			class="flex w-full items-start gap-3 rounded-lg p-3.5 text-left hover:bg-surface-gray-1 disabled:cursor-default disabled:hover:bg-transparent"
			@click="pick"
		>
			<span
				class="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border"
				:class="
					selected ? 'border-outline-gray-9 bg-surface-gray-9' : 'border-outline-gray-4'
				"
				aria-hidden="true"
			>
				<span v-if="selected" class="size-1.5 rounded-full bg-surface-base" />
			</span>
			<span class="min-w-0 flex-1">
				<span class="flex flex-wrap items-center gap-2">
					<span class="text-base font-medium text-ink-gray-8">{{
						suggestion.label
					}}</span>
					<Badge
						v-if="suggestion.recommended"
						label="Suggested"
						theme="blue"
						variant="subtle"
						size="sm"
					/>
					<Badge
						v-if="suggestion.safe"
						label="Safe"
						theme="gray"
						variant="outline"
						size="sm"
					/>
				</span>
				<span class="mt-1 block text-p-sm text-ink-gray-6">{{ suggestion.detail }}</span>
			</span>
			<span
				v-if="suggestion.confidence != null"
				class="shrink-0 text-sm tabular-nums text-ink-gray-5"
				>score {{ Number(suggestion.confidence).toFixed(2) }}</span
			>
		</button>
		<div v-if="selected && suggestion.options" class="px-3.5 pb-3.5 sm:pl-10">
			<Select
				v-model="value"
				:options="suggestion.options.map((o) => ({ label: o, value: o }))"
				placeholder="Choose a type"
				:disabled="disabled"
			/>
		</div>
		<div v-else-if="selected && suggestion.preview" class="px-3.5 pb-3.5 sm:pl-10">
			<p class="mb-1.5 text-xs text-ink-gray-5">How it will look on the wiki</p>
			<div
				class="max-h-96 overflow-auto rounded-md border border-outline-gray-1 bg-surface-base p-3"
			>
				<MarkdownPreview :content="suggestion.preview" />
			</div>
		</div>
	</div>
</template>

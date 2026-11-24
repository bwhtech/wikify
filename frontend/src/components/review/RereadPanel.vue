<script setup>
import { ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";

defineProps({
	running: { type: Boolean, default: false },
	error: { type: String, default: "" },
	disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["start"]);

const instruction = ref("");
</script>

<template>
	<div class="space-y-3">
		<FormControl
			v-model="instruction"
			type="textarea"
			:rows="2"
			label="Anything the model should do differently?"
			description="Optional. For example: the second column is a footnote, drop it."
			:disabled="disabled || running"
		/>
		<div class="flex flex-wrap items-center gap-3">
			<Button
				label="Re-read now"
				icon-left="lucide-scan-text"
				:loading="running"
				:disabled="disabled"
				@click="emit('start', instruction)"
			/>
			<span v-if="running" class="text-sm text-ink-gray-5">
				Reading the page. This takes 10 to 30 seconds.
			</span>
		</div>
		<Alert
			v-if="error"
			class="[overflow-wrap:anywhere]"
			theme="red"
			title="Could not re-read the page"
			:description="error"
		/>
	</div>
</template>

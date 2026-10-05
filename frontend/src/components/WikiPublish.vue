<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import {
	Badge,
	Button,
	Dialog,
	FormControl,
	TabButtons,
	dialog,
	useCall,
	useList,
	toast,
} from "frappe-ui";
import { useSocket } from "@/socket";
import { isPublished } from "@/utils/status";

const props = defineProps({
	importName: { type: String, required: true },
	status: { type: String, default: null },
	wikiSpace: { type: String, default: null },
});
const emit = defineEmits(["generated"]);

// Generation is gated on an approved tree (Graphed) or a stopped run.
const canPublish = computed(() =>
	["Graphed", "Generating Wiki", "Stopped"].includes(props.status)
);
const generating = computed(() => props.status === "Generating Wiki");
const alreadyGenerated = computed(() => props.status === "Completed" || !!props.wikiSpace);
// A completed publish is a one-way handoff — the API rejects a regenerate from here on,
// so the control disappears rather than sitting there disabled.
const published = computed(() => isPublished(props.status));

// Existing spaces (also resolves the current space's route for the "View wiki" link).
const spaces = useList({
	doctype: "Wiki Space",
	fields: ["name", "space_name", "route"],
	orderBy: "modified desc",
	limit: 100,
	auto: true,
});
const currentSpace = computed(() => (spaces.data || []).find((s) => s.name === props.wikiSpace));

// Target choice: reuse an existing space, or create a new one.
const mode = ref("existing");
const targetSpace = ref(null);
const newName = ref("");
const newRoute = ref("");
watch(
	() => spaces.data,
	(list) => {
		if (!list?.length) mode.value = "new";
		else if (!targetSpace.value) targetSpace.value = props.wikiSpace || list[0]?.name;
	},
	{ immediate: true }
);
// Suggest a slug-y route from the typed name.
watch(newName, (n) => {
	newRoute.value = (n || "")
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
});

const spaceOptions = computed(() =>
	(spaces.data || []).map((s) => ({ label: `${s.space_name} (/${s.route})`, value: s.name }))
);

const open = ref(false);

// Generate / regenerate.
const generate = useCall({
	url: "/api/v2/method/wikify.api.imports.generate_wiki",
	method: "POST",
	immediate: false,
});
const canGenerate = computed(() =>
	mode.value === "existing" ? !!targetSpace.value : !!(newName.value && newRoute.value)
);
async function runGenerate() {
	const params = { import_name: props.importName };
	if (mode.value === "existing") params.wiki_space = targetSpace.value;
	else params.new_space = { space_name: newName.value, route: newRoute.value };
	await generate.submit(params);
	if (generate.error) {
		toast.error(generate.error?.messages?.[0] || "Could not start wiki generation");
		return;
	}
	open.value = false;
	emit("generated");
}

const stop = useCall({
	url: "/api/v2/method/wikify.api.imports.stop_wiki_generation",
	method: "POST",
	immediate: false,
});
function stopGenerate() {
	dialog.danger({
		title: "Stop wiki generation",
		message:
			"Pages written so far stay in the wiki. Generating again starts over from the first page.",
		confirmLabel: "Stop",
		async onConfirm() {
			await stop.submit({ import_name: props.importName });
			if (stop.error) {
				throw new Error(stop.error?.messages?.[0] || "Could not stop wiki generation");
			}
			emit("generated");
		},
	});
}

// Realtime — the job emits wikify_wiki_done on completion.
const lastRoute = ref(null);
const socket = useSocket();
function onWikiDone(payload) {
	if (payload.import !== props.importName) return;
	lastRoute.value = payload.space_route;
	spaces.reload();
	emit("generated");
	toast.success("Wiki generated");
}
onMounted(() => socket?.on("wikify_wiki_done", onWikiDone));
onUnmounted(() => socket?.off("wikify_wiki_done", onWikiDone));

const wikiUrl = computed(() => {
	const route = lastRoute.value || currentSpace.value?.route;
	return route ? `/${route}` : null;
});
</script>

<template>
	<div class="flex shrink-0 items-center gap-2">
		<a
			v-if="alreadyGenerated && wikiUrl"
			:href="wikiUrl"
			target="_blank"
			class="inline-flex items-center gap-1 text-sm font-medium text-ink-blue-6 hover:underline"
		>
			{{ currentSpace?.space_name || "View wiki" }}
			<span aria-hidden>↗</span>
		</a>
		<Badge v-if="published" label="Published" theme="green" variant="subtle" size="sm" />
		<Button
			v-else
			size="sm"
			variant="subtle"
			:label="alreadyGenerated ? 'Regenerate' : 'Publish'"
			:disabled="!canPublish"
			:title="!canPublish ? 'Approve the section tree first' : undefined"
			@click="open = true"
		/>

		<Dialog
			v-model="open"
			:options="{ title: alreadyGenerated ? 'Regenerate wiki' : 'Publish wiki', size: 'lg' }"
		>
			<template #body-content>
				<p class="mb-4 text-sm text-ink-gray-5">
					Mirror the approved tree into a Wiki Space as linked pages.
				</p>
				<TabButtons
					v-model="mode"
					class="mb-4"
					:options="[
						{ label: 'Existing space', value: 'existing' },
						{ label: 'New space', value: 'new' },
					]"
				/>
				<template v-if="mode === 'existing'">
					<FormControl
						v-if="spaceOptions.length"
						type="select"
						label="Wiki Space"
						:options="spaceOptions"
						v-model="targetSpace"
					/>
					<p v-else class="text-sm text-ink-gray-5">No spaces yet — create one.</p>
				</template>
				<template v-else>
					<FormControl
						type="text"
						label="Space name"
						placeholder="My Manual"
						class="mb-3"
						v-model="newName"
					/>
					<FormControl
						type="text"
						label="Route"
						placeholder="my-manual"
						v-model="newRoute"
					/>
				</template>

				<div class="mt-5 flex items-center gap-2">
					<Button
						variant="solid"
						:label="alreadyGenerated ? 'Regenerate wiki' : 'Generate wiki'"
						:loading="generate.loading || generating"
						:disabled="!canGenerate || generating"
						@click="runGenerate"
					/>
					<Button
						v-if="generating"
						variant="ghost"
						label="Stop generating"
						icon-left="lucide-circle-stop"
						@click="stopGenerate"
					/>
				</div>
				<p v-if="generating" class="mt-2 text-xs text-ink-gray-5">
					Generating… watch progress in the header.
				</p>
			</template>
		</Dialog>
	</div>
</template>

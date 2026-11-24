<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { Alert, Badge, Button, LoadingIndicator, dayjs, dialog, toast, useCall } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import RereadPanel from "@/components/review/RereadPanel.vue";
import SuggestionCard from "@/components/review/SuggestionCard.vue";
import { CATEGORIES, issueLocation, severityBadge } from "@/components/review/meta";
import { setPage, setSection } from "@/data/agentContext";
import { useSocket } from "@/socket";

const props = defineProps({
	importName: { type: String, required: true },
	issueId: { type: String, required: true },
	queue: { type: Array, default: () => [] },
});
const emit = defineEmits(["open", "close", "changed"]);

const detail = useCall({
	url: "/api/v2/method/wikify.api.review.get_issue",
	params: () => ({ import_name: props.importName, issue_id: props.issueId }),
	refetch: true,
});
const issue = computed(() => detail.data);
const editable = computed(
	() => issue.value && !issue.value.read_only && issue.value.status === "open"
);

const choice = ref(null);
const value = ref("");
const showExtracted = ref(false);
const imageIndex = ref(0);
const reread = reactive({ running: false, error: "" });

watch(issue, (found) => {
	reread.running = Boolean(found?.reading);
	choice.value = reread.running
		? "reparse"
		: found?.suggestions.find((s) => s.recommended)?.id ?? null;
	reread.error = "";
	value.value = "";
	showExtracted.value = false;
	imageIndex.value = 0;
	attachAgentContext(found);
});

const position = computed(() => props.queue.findIndex((i) => i.id === props.issueId));
const prevId = computed(() => props.queue[position.value - 1]?.id);
const nextId = computed(() => props.queue[position.value + 1]?.id);
const settledTitle = computed(() => {
	const latest = issue.value?.history[0];
	if (!latest || latest.action === "accepted") return "Marked as fine";
	return `Fixed: ${latest.outcome}`;
});
const chosen = computed(() => issue.value?.suggestions.find((s) => s.id === choice.value));
const canApply = computed(
	() => chosen.value && !chosen.value.on_request && (!chosen.value.options || value.value)
);
const image = computed(() => issue.value?.images[imageIndex.value]);
const imageCount = computed(() => issue.value?.images.length || 0);

const applyCall = reviewCall("apply");
const acceptCall = reviewCall("accept");
const reopenCall = reviewCall("reopen");
const previewCall = reviewCall("preview_reparse");

async function startReread(instruction) {
	reread.error = "";
	reread.running = true;
	await previewCall.submit({
		import_name: props.importName,
		page_no: issue.value.target.page_no,
		instruction,
	});
	if (previewCall.error) {
		reread.running = false;
		reread.error = previewCall.error.messages?.[0] || "Could not start the re-read";
	}
}

function onPreview(payload) {
	const target = issue.value?.target;
	if (payload.import !== props.importName || target?.doctype !== "Source Page") return;
	if (payload.page_no !== target.page_no) return;
	reread.running = false;
	if (!payload.ok) {
		reread.error = payload.error;
		return;
	}
	toast.success(`Page re-read. The new version scores ${payload.composite.toFixed(2)}.`);
	detail.reload();
}

const socket = useSocket();
onMounted(() => socket?.on("wikify_review_preview", onPreview));
onUnmounted(() => socket?.off("wikify_review_preview", onPreview));

async function apply() {
	const result = await run(applyCall, { suggestion: choice.value, value: value.value });
	if (!result) return;
	emit("changed");
	if (result.follow_up?.length) showFollowUp(result);
	else toast.success(result.outcome);
	goNext();
}

function showFollowUp({ follow_up, follow_up_kind }) {
	const names = follow_up.map((s) => `'${s.title}'`).join(", ");
	const message =
		follow_up_kind === "shared_page"
			? `The page changed, but its wiki section did not. The page is shared by ${names}, so we could not tell where the new text belongs. Ask the agent to place it.`
			: `The section was rebuilt from whole pages. It shares a page with ${names}, so some text may now appear twice. Check those sections in the Wiki tab.`;
	dialog.confirm({
		title: "Fix applied. One thing to check.",
		message,
		theme: "yellow",
		actions: [{ label: "Got it", variant: "solid" }],
	});
}

async function accept() {
	if (!(await run(acceptCall))) return;
	emit("changed");
	goNext();
}

async function reopen() {
	if (!(await run(reopenCall))) return;
	emit("changed");
	detail.reload();
}

function goNext() {
	const next =
		props.queue.find((i, index) => index > position.value) ||
		props.queue.find((i) => i.id !== props.issueId);
	if (next) emit("open", next.id);
	else emit("close");
}

async function run(call, params = {}) {
	await call.submit({ import_name: props.importName, issue_id: props.issueId, ...params });
	if (call.error) {
		toast.error(call.error.messages?.[0] || "Something went wrong");
		return null;
	}
	return call.data;
}

function reviewCall(method) {
	return useCall({
		url: `/api/v2/method/wikify.api.review.${method}`,
		method: "POST",
		immediate: false,
	});
}

function attachAgentContext(found) {
	if (!found) return;
	const { doctype, name, page_no, section } = found.target;
	if (doctype === "Source Page") setPage({ name, label: `Page ${page_no}` });
	if (doctype === "Source Section") setSection({ name, label: section });
}

function decisionText(decision) {
	const what =
		decision.action === "accepted" ? "Marked as fine" : decision.outcome || "Fix applied";
	const asked = decision.instruction ? ` (you asked: "${decision.instruction}")` : "";
	return `${what}${asked}, ${dayjs(decision.creation).fromNow()}`;
}
</script>

<template>
	<div class="flex h-[calc(100dvh-9.5rem)] min-h-0 flex-col sm:h-[calc(100vh-7rem)]">
		<div
			class="flex shrink-0 items-center gap-1 border-b border-outline-gray-1 px-3 py-1.5 sm:px-5"
		>
			<Button
				variant="ghost"
				icon-left="lucide-arrow-left"
				label="All spots"
				@click="emit('close')"
			/>
			<span v-if="position >= 0" class="ml-auto mr-2 text-sm tabular-nums text-ink-gray-5">
				{{ position + 1 }} of {{ queue.length }} open
			</span>
			<Button
				:class="position < 0 && 'ml-auto'"
				variant="ghost"
				icon="lucide-chevron-left"
				label="Previous spot"
				:disabled="!prevId"
				@click="emit('open', prevId)"
			/>
			<Button
				variant="ghost"
				icon="lucide-chevron-right"
				label="Next spot"
				:disabled="!nextId"
				@click="emit('open', nextId)"
			/>
		</div>

		<div v-if="detail.loading && !issue" class="flex flex-1 items-center justify-center">
			<LoadingIndicator class="size-5 text-ink-gray-5" />
		</div>

		<div
			v-else-if="detail.error"
			class="flex flex-1 flex-col items-center justify-center gap-3 text-center"
		>
			<p class="text-base text-ink-gray-7">
				{{ detail.error.messages?.[0] || "Could not load this item" }}
			</p>
			<Button label="Back to all spots" @click="emit('close')" />
		</div>

		<div
			v-else-if="issue"
			class="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden"
		>
			<aside
				v-if="issue.images.length"
				class="shrink-0 border-b border-outline-gray-1 bg-surface-gray-1 px-3 py-4 sm:px-5 lg:w-[42%] lg:overflow-y-auto lg:border-b-0 lg:border-r"
			>
				<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
					<div class="flex items-center gap-1">
						<p class="text-sm text-ink-gray-6">
							PDF page {{ image?.page_no
							}}<template v-if="imageCount > 1">
								({{ imageIndex + 1 }} of {{ imageCount }})</template
							>
						</p>
						<template v-if="imageCount > 1">
							<Button
								variant="ghost"
								size="sm"
								icon="lucide-chevron-left"
								label="Previous page"
								:disabled="imageIndex === 0"
								@click="imageIndex--"
							/>
							<Button
								variant="ghost"
								size="sm"
								icon="lucide-chevron-right"
								label="Next page"
								:disabled="imageIndex === imageCount - 1"
								@click="imageIndex++"
							/>
						</template>
					</div>
					<Button
						variant="ghost"
						size="sm"
						icon-left="lucide-external-link"
						label="Open in Pages"
						:route="{
							name: 'ImportDetail',
							params: { name: importName, tab: 'pages' },
							query: { page: image?.page_no },
						}"
					/>
				</div>
				<img
					v-if="image"
					:src="image.image"
					:alt="`PDF page ${image.page_no}`"
					class="mx-auto w-full max-w-[240px] rounded-sm border border-outline-gray-2 bg-surface-white shadow-sm lg:max-w-none"
				/>
			</aside>

			<main class="flex flex-1 flex-col lg:min-h-0 lg:overflow-hidden">
				<div
					class="flex-1 space-y-6 px-3 pt-5 pb-10 sm:px-6 lg:min-h-0 lg:overflow-y-auto"
				>
					<section class="space-y-3">
						<div class="flex flex-wrap items-center gap-2">
							<Badge
								:label="CATEGORIES[issue.category].label"
								theme="gray"
								variant="subtle"
							/>
							<Badge v-bind="severityBadge(issue)" variant="subtle" />
							<span class="text-sm text-ink-gray-5">
								{{ issueLocation(issue)
								}}<template v-if="issue.target.section">
									· {{ issue.target.section }}</template
								>
							</span>
						</div>
						<h2 class="text-xl font-semibold text-ink-gray-9">{{ issue.title }}</h2>
						<p class="max-w-[640px] text-p-base text-ink-gray-7">{{ issue.reason }}</p>
						<div class="flex flex-wrap gap-x-5 gap-y-1.5">
							<div
								v-for="signal in issue.signals"
								:key="signal.label"
								class="text-sm"
							>
								<span class="text-ink-gray-5">{{ signal.label }}</span>
								<span
									class="ml-1.5 tabular-nums text-ink-gray-8"
									:class="signal.mono && 'font-mono'"
									>{{ signal.value }}</span
								>
							</div>
						</div>
						<div v-if="issue.markdown != null">
							<Button
								variant="ghost"
								size="sm"
								:icon-left="
									showExtracted ? 'lucide-chevron-down' : 'lucide-chevron-right'
								"
								label="What we extracted"
								@click="showExtracted = !showExtracted"
							/>
							<div
								v-if="showExtracted"
								class="mt-2 max-h-80 overflow-auto rounded-md border border-outline-gray-1 p-3"
							>
								<MarkdownPreview :content="issue.markdown" />
							</div>
						</div>
						<ul v-if="issue.history.length" class="space-y-1">
							<li
								v-for="decision in issue.history"
								:key="decision.name"
								class="text-sm text-ink-gray-5"
							>
								Tried: {{ decisionText(decision) }}
							</li>
						</ul>
					</section>

					<Alert
						v-if="issue.read_only"
						theme="blue"
						title="Published. Edit pages in the Wiki app."
					/>
					<Alert
						v-else-if="issue.status === 'settled'"
						theme="green"
						:title="settledTitle"
					>
						<template #footer>
							<Button
								label="Reopen"
								size="sm"
								:loading="reopenCall.loading"
								@click="reopen"
							/>
						</template>
					</Alert>

					<section class="space-y-3">
						<h3 class="text-lg font-semibold text-ink-gray-8">{{ issue.question }}</h3>
						<div role="radiogroup" :aria-label="issue.question" class="space-y-2">
							<SuggestionCard
								v-for="suggestion in issue.suggestions"
								:key="suggestion.id"
								v-model:value="value"
								:suggestion="suggestion"
								:selected="choice === suggestion.id"
								:disabled="!editable"
								@select="choice = suggestion.id"
							>
								<RereadPanel
									:running="reread.running"
									:error="reread.error"
									:disabled="!editable"
									@start="startReread"
								/>
							</SuggestionCard>
						</div>
						<p v-if="!issue.suggestions.length" class="text-p-sm text-ink-gray-5">
							We have no automatic fix for this one. Mark it as fine, or ask the
							agent in the chat panel.
						</p>
					</section>
				</div>

				<footer
					v-if="editable"
					class="sticky bottom-0 flex shrink-0 flex-wrap items-center gap-2 border-t border-outline-gray-1 bg-surface-base px-3 py-3 sm:px-6"
				>
					<Button
						variant="ghost"
						label="Skip for now"
						:disabled="!nextId"
						@click="emit('open', nextId)"
					/>
					<Button
						variant="subtle"
						label="It is fine as is"
						:loading="acceptCall.loading"
						@click="accept"
					/>
					<Button
						class="ml-auto"
						variant="solid"
						theme="gray"
						icon-right="lucide-arrow-right"
						label="Apply and next"
						:disabled="!canApply"
						:loading="applyCall.loading"
						@click="apply"
					/>
				</footer>
			</main>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import {
	Alert,
	Badge,
	Button,
	Progress,
	Skeleton,
	TabButtons,
	dayjs,
	dialog,
	toast,
	useCall,
} from "frappe-ui";
import ReviewPageMap from "@/components/review/ReviewPageMap.vue";
import { CATEGORIES, issueLocation, severityBadge } from "@/components/review/meta";
import { plural } from "@/utils/format";

const props = defineProps({
	importName: { type: String, required: true },
	review: { type: Object, default: null },
	issues: { type: Array, default: () => [] },
	loading: { type: Boolean, default: false },
});
const emit = defineEmits(["open", "open-page", "changed"]);

const applySafe = useCall({
	url: "/api/v2/method/wikify.api.review.apply_safe",
	method: "POST",
	immediate: false,
});

function confirmApplySafe() {
	const count = summary.value.safe;
	dialog.confirm({
		title: `Apply ${plural(count, "safe fix", "safe fixes")}?`,
		message:
			"These only add missing markup, such as a table header line or a closing code fence. No text is rewritten, and each fix shows under Reviewed.",
		confirmLabel: "Apply fixes",
		async onConfirm() {
			await applySafe.submit({ import_name: props.importName });
			if (applySafe.error)
				throw new Error(applySafe.error.messages?.[0] || "Could not apply");
			toast.success(`Applied ${plural(applySafe.data.applied, "fix", "fixes")}`);
			emit("changed");
		},
	});
}

const summary = computed(() => props.review?.summary);
const total = computed(() => (summary.value ? summary.value.open + summary.value.reviewed : 0));
const progress = computed(() =>
	total.value ? Math.round((summary.value.reviewed / total.value) * 100) : 100
);

const kpis = computed(() => {
	const s = summary.value;
	return [
		{ label: "Pages parsed", value: s.pages },
		{ label: "Ready for wiki", value: s.ready, hint: `${percent(s.ready, s.pages)} of pages` },
		{
			label: "Improved automatically",
			value: s.improved,
			hint: "vision or cleanup pass",
		},
		{ label: "Needs review", value: s.open },
		{ label: "Blocks publish", value: s.blocks, danger: s.blocks > 0 },
	];
});

const tab = ref("open");
const tabOptions = computed(() => [
	{ label: `Open ${summary.value?.open ?? 0}`, value: "open" },
	{ label: `Reviewed ${summary.value?.reviewed ?? 0}`, value: "reviewed" },
]);

const groups = computed(() =>
	Object.entries(CATEGORIES)
		.map(([key, meta]) => ({
			key,
			...meta,
			items: props.issues.filter((i) => i.category === key),
		}))
		.filter((g) => g.items.length)
);

function percent(part, whole) {
	return whole ? `${Math.round((part / whole) * 100)}%` : "0%";
}

function decisionLabel(decision) {
	if (decision.action === "accepted") return "Marked as fine";
	return decision.outcome || "Fix applied";
}
</script>

<template>
	<div class="body-container space-y-8 pt-5 pb-40">
		<template v-if="loading">
			<Skeleton class="h-6 w-2/3 rounded" />
			<Skeleton class="h-20 w-full rounded" />
			<Skeleton class="h-24 w-full rounded" />
			<Skeleton class="h-64 w-full rounded" />
		</template>

		<template v-else-if="summary">
			<section class="space-y-3">
				<p v-if="summary.open" class="text-p-base text-ink-gray-7">
					We parsed all {{ summary.pages }} pages. {{ summary.ready }} are ready for the
					wiki. {{ plural(summary.open, "spot needs", "spots need") }} a decision from
					you. Each one comes with a suggested fix.
				</p>
				<p v-else class="text-p-base text-ink-gray-7">
					Nothing left to review. All {{ summary.pages }} pages are ready for the wiki.
				</p>
				<div v-if="total" class="flex items-center gap-3">
					<Progress class="flex-1" :value="progress" size="sm" />
					<span class="shrink-0 text-sm tabular-nums text-ink-gray-5">
						{{ summary.reviewed }} of {{ total }} reviewed
					</span>
				</div>
			</section>

			<div
				class="grid grid-cols-2 gap-y-4 rounded-md border border-outline-gray-1 py-4 sm:grid-cols-5 sm:divide-x sm:divide-outline-gray-2"
			>
				<div v-for="kpi in kpis" :key="kpi.label" class="px-4">
					<p class="text-sm text-ink-gray-5">{{ kpi.label }}</p>
					<p
						class="mt-1.5 text-2xl font-semibold tabular-nums"
						:class="kpi.danger ? 'text-ink-red-5' : 'text-ink-gray-9'"
					>
						{{ kpi.value }}
					</p>
					<p v-if="kpi.hint" class="mt-1 text-xs text-ink-gray-5">{{ kpi.hint }}</p>
				</div>
			</div>

			<Alert
				v-if="review.read_only"
				theme="blue"
				title="This wiki is published"
				description="You can read the review, but fixes now happen in the Wiki app."
			/>

			<Alert
				v-else-if="summary.blocks"
				theme="red"
				:title="`${plural(summary.blocks, 'spot blocks', 'spots block')} publishing`"
				description="They would render broken or lose content on the wiki. Fix these first."
			/>

			<div
				v-if="!review.read_only && summary.safe"
				class="flex flex-col gap-3 rounded-md border border-outline-gray-1 bg-surface-gray-1 p-4 sm:flex-row sm:items-center"
			>
				<div class="flex flex-1 items-start gap-3">
					<span
						class="lucide-wand-sparkles mt-0.5 size-4 shrink-0 text-ink-gray-6"
						aria-hidden="true"
					/>
					<div>
						<p class="text-base font-medium text-ink-gray-8">
							{{ plural(summary.safe, "spot has", "spots have") }} a safe, mechanical
							fix
						</p>
						<p class="mt-1 text-p-sm text-ink-gray-6">
							Adding a missing table header line or closing a code block. Nothing is
							rewritten.
						</p>
					</div>
				</div>
				<Button
					:label="`Apply ${plural(summary.safe, 'safe fix', 'safe fixes')}`"
					@click="confirmApplySafe"
				/>
			</div>

			<ReviewPageMap
				:tiles="review.page_map"
				@open="emit('open', $event)"
				@open-page="emit('open-page', $event)"
			/>

			<section class="space-y-4">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h2 class="text-lg font-semibold text-ink-gray-8">Review queue</h2>
					<TabButtons v-model="tab" :options="tabOptions" />
				</div>

				<template v-if="tab === 'open'">
					<div
						v-if="!groups.length"
						class="flex flex-col items-center justify-center gap-3 py-16 text-center"
					>
						<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
							<span class="lucide-circle-check size-6" aria-hidden="true" />
						</div>
						<p class="text-base text-ink-gray-7">Nothing left to review</p>
						<p class="text-sm text-ink-gray-5">All pages are ready for the wiki.</p>
					</div>

					<div v-for="group in groups" :key="group.key" class="space-y-1.5">
						<div class="flex items-center gap-2 px-1">
							<span
								:class="group.icon"
								class="size-4 text-ink-gray-5"
								aria-hidden="true"
							/>
							<h3 class="text-base font-medium text-ink-gray-8">
								{{ group.label }}
							</h3>
							<span class="text-sm tabular-nums text-ink-gray-5">{{
								group.items.length
							}}</span>
						</div>
						<div class="rounded-md border border-outline-gray-1">
							<button
								v-for="issue in group.items"
								:key="issue.id"
								class="flex w-full flex-wrap items-center gap-x-4 gap-y-1 border-b border-outline-gray-1 px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-gray-2 sm:flex-nowrap"
								@click="emit('open', issue.id)"
							>
								<span
									class="w-20 shrink-0 whitespace-nowrap text-sm tabular-nums text-ink-gray-5"
									>{{ issueLocation(issue) }}</span
								>
								<span class="min-w-0 flex-1">
									<span class="block truncate text-base text-ink-gray-8">{{
										issue.title
									}}</span>
									<span
										v-if="issue.target.section"
										class="mt-0.5 block truncate text-sm text-ink-gray-5"
										>{{ issue.target.section }}</span
									>
								</span>
								<span
									v-if="issue.suggested"
									class="hidden w-60 shrink-0 items-center gap-1.5 text-sm text-ink-gray-6 md:flex"
								>
									<span
										class="lucide-sparkles size-3.5 shrink-0 text-ink-gray-4"
										aria-hidden="true"
									/>
									<span class="truncate">{{ issue.suggested }}</span>
								</span>
								<Badge
									v-bind="severityBadge(issue)"
									variant="subtle"
									class="shrink-0"
								/>
							</button>
						</div>
					</div>
				</template>

				<template v-else>
					<p
						v-if="!review.reviewed.length"
						class="py-16 text-center text-base text-ink-gray-7"
					>
						Nothing reviewed yet. Spots you fix or mark as fine show up here.
					</p>
					<div v-else class="rounded-md border border-outline-gray-1">
						<button
							v-for="decision in review.reviewed"
							:key="decision.name"
							class="flex w-full flex-wrap items-center gap-x-4 gap-y-1 border-b border-outline-gray-1 px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-gray-2 sm:flex-nowrap"
							@click="emit('open', decision.issue_id)"
						>
							<span class="min-w-0 flex-1 truncate text-base text-ink-gray-8">{{
								decision.issue_title
							}}</span>
							<span class="shrink-0 truncate text-sm text-ink-gray-6">{{
								decisionLabel(decision)
							}}</span>
							<span class="shrink-0 text-sm text-ink-gray-5">{{
								dayjs(decision.creation).fromNow()
							}}</span>
						</button>
					</div>
				</template>
			</section>
		</template>
	</div>
</template>

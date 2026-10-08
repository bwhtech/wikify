<script setup>
import { computed, ref, watch } from "vue";
import { Button, Dialog, LoadingIndicator, useCall } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import { pageRange } from "@/utils/format";

const props = defineProps({
	section: { type: String, default: null },
});
const emit = defineEmits(["update:section"]);

const history = ref([]);
const pdfPage = ref(null);
const detail = useCall({
	url: "/api/v2/method/wikify.api.dashboard.section_detail",
	method: "GET",
	immediate: false,
});

const open = computed({
	get: () => !!props.section,
	set: (value) => {
		if (!value) emit("update:section", null);
	},
});

watch(
	() => props.section,
	(name) => {
		if (!name) {
			history.value = [];
			pdfPage.value = null;
			return;
		}
		detail.submit({ name });
	},
	{ immediate: true }
);

const data = computed(() => (detail.data?.name === props.section ? detail.data : null));
const ancestors = computed(() => {
	const path = data.value?.hierarchy_path || "";
	return path.split(" > ").slice(0, -1);
});

function go(name) {
	history.value.push(props.section);
	emit("update:section", name);
}

function back() {
	emit("update:section", history.value.pop());
}

const pdfSrc = computed(() =>
	data.value?.pdf && pdfPage.value
		? `${data.value.pdf}#page=${pdfPage.value}&navpanes=0&view=FitH`
		: null
);

watch(data, (section) => {
	if (pdfPage.value && section?.page_start) pdfPage.value = section.page_start;
});

function openPdfTab() {
	window.open(pdfSrc.value, "_blank", "noopener");
}

function showPdf(page) {
	pdfPage.value = Math.max(1, page);
}
</script>

<template>
	<Dialog v-model:open="open" :size="pdfPage ? '7xl' : '4xl'">
		<div class="flex gap-5">
			<div class="min-w-0 flex-1 space-y-4">
				<div class="flex items-center gap-2 pr-8">
					<Button
						v-if="history.length"
						variant="ghost"
						icon="lucide-arrow-left"
						label="Back"
						@click="back"
					/>
					<LoadingIndicator v-if="detail.loading" class="size-4 text-ink-gray-5" />
				</div>

				<template v-if="data">
					<div>
						<p v-if="ancestors.length" class="truncate text-sm text-ink-gray-5">
							{{ ancestors.join(" › ") }}
						</p>
						<h2 class="mt-1 text-2xl font-semibold text-ink-gray-9">
							{{ data.title }}
						</h2>
						<div
							class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-gray-6"
						>
							<span v-if="data.category" class="flex items-center gap-1.5">
								<span
									class="size-2 rounded-full"
									:style="{ backgroundColor: data.category_color || '#9ca3af' }"
									aria-hidden="true"
								/>
								{{ data.category }}
							</span>
							<span class="flex items-center gap-1.5">
								<span class="lucide-folder size-3.5" aria-hidden="true" />
								{{ data.project_name }}
							</span>
							<span class="flex items-center gap-1.5">
								<span class="lucide-file-text size-3.5" aria-hidden="true" />
								{{ data.doc_title }}
							</span>
							<Button
								v-if="pageRange(data)"
								size="sm"
								variant="subtle"
								icon-left="lucide-file-search"
								:label="pageRange(data)"
								:tooltip="data.pdf ? 'Show in original PDF' : 'No PDF attached'"
								:disabled="!data.pdf"
								@click="showPdf(data.page_start)"
							/>
						</div>
					</div>

					<div class="max-h-[65vh] overflow-y-auto border-t border-outline-gray-1 pt-4">
						<MarkdownPreview v-if="data.markdown" :content="data.markdown" />
						<p v-else class="py-6 text-center text-sm text-ink-gray-5">
							This section has no content of its own.
						</p>

						<div v-if="data.children.length" class="mt-6">
							<h3 class="mb-2 text-sm font-medium text-ink-gray-7">Subsections</h3>
							<div
								class="divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-2"
							>
								<button
									v-for="c in data.children"
									:key="c.name"
									class="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-gray-1"
									@click="go(c.name)"
								>
									<span
										class="lucide-file-text size-4 shrink-0 text-ink-gray-5"
										aria-hidden="true"
									/>
									<span
										class="min-w-0 flex-1 truncate text-sm text-ink-gray-8"
										>{{ c.title }}</span
									>
									<span
										v-if="pageRange(c)"
										class="text-xs text-ink-gray-5 tabular-nums"
									>
										{{ pageRange(c) }}
									</span>
									<span
										class="lucide-chevron-right size-4 text-ink-gray-4"
										aria-hidden="true"
									/>
								</button>
							</div>
						</div>
					</div>
				</template>
			</div>

			<Transition
				enter-active-class="transition duration-300 ease-out"
				enter-from-class="translate-x-8 opacity-0"
				leave-active-class="transition duration-150 ease-in"
				leave-to-class="translate-x-8 opacity-0"
			>
				<aside
					v-if="pdfSrc"
					class="mt-8 flex h-[76vh] w-1/2 shrink-0 flex-col overflow-hidden rounded-lg border border-outline-gray-2"
				>
					<div class="flex items-center gap-1 border-b border-outline-gray-1 px-3 py-2">
						<span class="lucide-file-text size-4 text-ink-gray-5" aria-hidden="true" />
						<span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">
							Original PDF · page {{ pdfPage }}
						</span>
						<Button
							variant="ghost"
							icon="lucide-chevron-left"
							tooltip="Previous page"
							:disabled="pdfPage <= 1"
							@click="showPdf(pdfPage - 1)"
						/>
						<Button
							variant="ghost"
							icon="lucide-chevron-right"
							tooltip="Next page"
							@click="showPdf(pdfPage + 1)"
						/>
						<Button
							variant="ghost"
							icon="lucide-external-link"
							tooltip="Open in a new tab"
							@click="openPdfTab"
						/>
						<Button
							variant="ghost"
							icon="lucide-x"
							tooltip="Close PDF"
							@click="pdfPage = null"
						/>
					</div>
					<object
						:key="pdfSrc"
						:data="pdfSrc"
						type="application/pdf"
						class="min-h-0 w-full flex-1"
					>
						<p class="p-4 text-sm text-ink-gray-5">
							Can't embed the PDF here —
							<a :href="pdfSrc" target="_blank" class="underline"
								>open it in a new tab</a
							>.
						</p>
					</object>
				</aside>
			</Transition>
		</div>
	</Dialog>
</template>

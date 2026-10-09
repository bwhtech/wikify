<script setup>
import { computed, ref, watch } from "vue";
import { LoadingIndicator, TextInput, useCall } from "frappe-ui";
import AppPageHeader from "@/components/AppPageHeader.vue";
import SectionDialog from "@/components/SectionDialog.vue";
import { pageRange, plural } from "@/utils/format";

const project = ref("");
const category = ref("");
const subcategory = ref("");
const projectSearch = ref("");
const openSection = ref(null);

const summary = useCall({
	url: "/api/v2/method/wikify.api.dashboard.summary",
	method: "GET",
	immediate: false,
});

watch(project, () => (category.value = ""), { flush: "sync" });
watch(category, () => (subcategory.value = ""), { flush: "sync" });
watch(
	[project, category, subcategory],
	([p, c, s]) => {
		const params = {};
		if (p) params.project = p;
		if (c) params.section_type = c;
		if (s) params.subcategory = s;
		summary.submit(params);
	},
	{ immediate: true }
);

const data = computed(() => summary.data || {});
const projects = computed(() => data.value.projects || []);
const categories = computed(() => data.value.categories || []);
const subcategories = computed(() => data.value.subcategories || []);
const sections = computed(() => data.value.sections || []);

const cards = computed(() => {
	const c = data.value.cards || {};
	return [
		{ label: "Projects", icon: "lucide-folder", value: c.projects },
		{ label: "Documents", icon: "lucide-file-stack", value: c.documents },
		{ label: "Categories", icon: "lucide-tags", value: c.categories },
		{ label: "Sections", icon: "lucide-list-tree", value: c.sections },
	];
});

const visibleProjects = computed(() => {
	const q = projectSearch.value.trim().toLowerCase();
	return q
		? projects.value.filter((p) => p.project_name.toLowerCase().includes(q))
		: projects.value;
});
const projectRows = computed(() => [
	{
		name: "",
		project_name: "All projects",
		documents: data.value.cards?.documents,
		icon: "lucide-layers",
	},
	...visibleProjects.value.map((p) => ({ ...p, icon: "lucide-folder" })),
]);
const projectName = computed(
	() => projects.value.find((p) => p.name === project.value)?.project_name
);
const categoryLabel = computed(
	() => categories.value.find((c) => c.type_name === category.value)?.label || category.value
);
const maxSections = computed(() => Math.max(1, ...categories.value.map((c) => c.sections)));

const level = computed(() => {
	if (subcategory.value) return "sections";
	if (category.value) return "subcategories";
	return "categories";
});

function reset() {
	project.value = "";
	category.value = "";
	subcategory.value = "";
}

const trail = computed(() => {
	const items = [{ label: "All projects", go: reset }];
	if (project.value)
		items.push({ label: projectName.value || project.value, go: () => (category.value = "") });
	if (category.value)
		items.push({ label: categoryLabel.value, go: () => (subcategory.value = "") });
	if (subcategory.value) items.push({ label: subcategory.value });
	return items;
});
</script>

<template>
	<div class="flex flex-col">
		<AppPageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="shrink-0 text-md text-ink-gray-9">Dashboard</h1>
				<LoadingIndicator v-if="summary.loading" class="size-4 text-ink-gray-5" />
			</div>
		</AppPageHeader>

		<div class="mx-auto w-full max-w-6xl space-y-6 px-3 pt-5 pb-16 sm:px-5">
			<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
				<div
					v-for="c in cards"
					:key="c.label"
					class="rounded-lg border border-outline-gray-2 bg-surface-base p-4"
				>
					<div class="flex items-center gap-1.5 text-sm text-ink-gray-5">
						<span :class="c.icon" class="size-4" aria-hidden="true" />
						{{ c.label }}
					</div>
					<div class="mt-2 text-3xl font-semibold text-ink-gray-9 tabular-nums">
						{{ c.value ?? "—" }}
					</div>
				</div>
			</div>

			<div class="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
				<aside class="flex flex-col rounded-lg border border-outline-gray-2 p-2">
					<div class="px-0.5 pt-1.5 pb-2">
						<TextInput v-model="projectSearch" placeholder="Find a project">
							<template #prefix>
								<span
									class="lucide-search size-4 text-ink-gray-5"
									aria-hidden="true"
								/>
							</template>
						</TextInput>
					</div>
					<div class="max-h-[32rem] overflow-y-auto">
						<button
							v-for="p in projectRows"
							:key="p.name"
							class="flex w-full items-center gap-2 rounded px-2.5 py-2 text-left"
							:class="
								project === p.name
									? 'bg-surface-gray-3 text-ink-gray-9'
									: 'text-ink-gray-7 hover:bg-surface-gray-2'
							"
							@click="project = p.name"
						>
							<span
								:class="p.icon"
								class="size-4 shrink-0 text-ink-gray-5"
								aria-hidden="true"
							/>
							<span class="flex-1 truncate text-sm">{{ p.project_name }}</span>
							<span class="text-xs text-ink-gray-5 tabular-nums">{{
								p.documents
							}}</span>
						</button>
					</div>
				</aside>

				<section class="min-w-0 rounded-lg border border-outline-gray-2">
					<header
						class="flex min-h-11 flex-wrap items-center gap-1 border-b border-outline-gray-1 px-4 py-2 text-sm"
					>
						<template v-for="(t, i) in trail" :key="t.label">
							<span
								v-if="i"
								class="lucide-chevron-right size-3.5 text-ink-gray-4"
								aria-hidden="true"
							/>
							<button
								v-if="i < trail.length - 1"
								class="text-ink-gray-5 hover:text-ink-gray-8"
								@click="t.go"
							>
								{{ t.label }}
							</button>
							<span v-else class="font-medium text-ink-gray-9">{{ t.label }}</span>
						</template>
					</header>

					<div class="p-4">
						<template v-if="level === 'categories'">
							<div
								v-if="categories.length"
								class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
							>
								<button
									v-for="c in categories"
									:key="c.type_name"
									class="rounded-lg border border-outline-gray-2 p-4 text-left hover:bg-surface-gray-1"
									@click="category = c.type_name"
								>
									<div class="flex items-center gap-2">
										<span
											class="size-2.5 shrink-0 rounded-full"
											:style="{ backgroundColor: c.color }"
											aria-hidden="true"
										/>
										<span
											class="truncate text-base font-medium text-ink-gray-8"
											:title="c.label"
										>
											{{ c.label }}
										</span>
										<span
											class="ml-auto text-xl font-semibold text-ink-gray-9 tabular-nums"
										>
											{{ c.sections }}
										</span>
									</div>
									<div
										class="mt-3 h-1 overflow-hidden rounded-full bg-surface-gray-2"
									>
										<div
											class="h-full rounded-full"
											:style="{
												width: `${(c.sections / maxSections) * 100}%`,
												backgroundColor: c.color,
											}"
										/>
									</div>
									<div class="mt-2 flex gap-3 text-xs text-ink-gray-5">
										<span>{{
											plural(c.subcategories, "subcategory", "subcategories")
										}}</span>
										<span>{{ plural(c.documents, "document") }}</span>
									</div>
								</button>
							</div>
							<div
								v-else-if="!summary.loading"
								class="flex flex-col items-center gap-3 py-12 text-center"
							>
								<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
									<span class="lucide-tags size-6" aria-hidden="true" />
								</div>
								<p class="text-base text-ink-gray-7">No categories yet</p>
								<p class="text-sm text-ink-gray-5">
									Sections are categorised once a document is parsed.
								</p>
							</div>
						</template>

						<div
							v-else-if="level === 'subcategories'"
							class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
						>
							<button
								v-for="s in subcategories"
								:key="s.title"
								class="rounded-lg border border-outline-gray-2 p-4 text-left hover:bg-surface-gray-1"
								@click="subcategory = s.title"
							>
								<div class="flex items-center gap-2">
									<span
										class="lucide-tag size-4 shrink-0 text-ink-gray-5"
										aria-hidden="true"
									/>
									<span
										class="truncate text-base font-medium text-ink-gray-8"
										:title="s.title"
									>
										{{ s.title }}
									</span>
									<span
										class="ml-auto text-xl font-semibold text-ink-gray-9 tabular-nums"
									>
										{{ s.sections }}
									</span>
								</div>
								<div class="mt-2 text-xs text-ink-gray-5">
									{{ plural(s.documents, "document") }}
								</div>
							</button>
						</div>

						<div v-else class="-m-4 divide-y divide-outline-gray-1">
							<button
								v-for="s in sections"
								:key="s.name"
								class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-gray-1"
								@click="openSection = s.name"
							>
								<span
									class="lucide-file-text size-4 shrink-0 text-ink-gray-5"
									aria-hidden="true"
								/>
								<div class="min-w-0 flex-1">
									<div class="truncate text-sm text-ink-gray-8">
										{{ s.title }}
									</div>
									<div class="mt-0.5 truncate text-xs text-ink-gray-5">
										{{ s.doc_title }} · {{ s.hierarchy_path || s.title }}
									</div>
								</div>
								<span
									v-if="pageRange(s)"
									class="text-xs text-ink-gray-5 tabular-nums"
								>
									{{ pageRange(s) }}
								</span>
								<span
									class="lucide-chevron-right size-4 text-ink-gray-4"
									aria-hidden="true"
								/>
							</button>
						</div>
					</div>
				</section>
			</div>
		</div>
		<SectionDialog v-model:section="openSection" />
	</div>
</template>

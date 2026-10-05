<script setup>
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Badge, Button, PageHeader, useDoc } from "frappe-ui";
import ImportList from "@/pages/ImportList.vue";
import NewImportDialog from "@/components/NewImportDialog.vue";
import { useIsMobile } from "@/composables/useMediaQuery";
import { actionButtonProps } from "@/utils/actionButton";
import { setProject } from "@/data/agentContext";
import { recordRecent } from "@/data/commandPalette";

const props = defineProps({
	name: { type: String, required: true },
});

const isMobile = useIsMobile();

const project = useDoc({ doctype: "Wikify Project", name: props.name });

// Attach this project as the agent's default context.
watch(
	() => props.name,
	(name) => name && setProject({ name, label: project.doc?.project_name || name }),
	{ immediate: true }
);
watch(
	() => project.doc?.project_name,
	(label) => {
		if (!label) return;
		setProject({ name: props.name, label });
		recordRecent({ kind: "project", name: props.name, label });
	}
);

const route = useRoute();
const router = useRouter();
const showNewImport = ref(false);

watch(
	() => route.query.upload,
	(upload) => {
		if (!upload) return;
		showNewImport.value = true;
		const { upload: _, ...query } = route.query;
		router.replace({ query });
	},
	{ immediate: true }
);
</script>

<template>
	<div>
		<PageHeader>
			<div class="flex min-w-0 items-center gap-2">
				<Button variant="ghost" icon="lucide-arrow-left" :route="{ name: 'Projects' }" />
				<nav class="flex min-w-0 items-center gap-1.5 text-base">
					<RouterLink
						:to="{ name: 'Projects' }"
						class="hidden text-ink-gray-5 hover:text-ink-gray-7 sm:block"
						>Projects</RouterLink
					>
					<span class="hidden text-ink-gray-4 sm:block" aria-hidden="true">/</span>
					<span class="truncate text-ink-gray-9">{{
						project.doc?.project_name || name
					}}</span>
				</nav>
				<Badge
					v-if="project.doc?.is_default"
					label="Default"
					theme="gray"
					variant="subtle"
					size="sm"
					class="shrink-0"
				/>
			</div>

			<div class="flex shrink-0 items-center gap-2 pl-2">
				<Button
					variant="subtle"
					v-bind="actionButtonProps(isMobile, 'lucide-waypoints', 'Graph')"
					:route="{ name: 'ProjectGraph', params: { name } }"
				/>
				<Button
					variant="ghost"
					icon="lucide-settings"
					aria-label="Project settings"
					:route="{ name: 'ProjectSettings', params: { name } }"
				/>
				<Button
					variant="solid"
					theme="gray"
					v-bind="actionButtonProps(isMobile, 'lucide-plus', 'New Document')"
					@click="showNewImport = true"
				/>
			</div>
		</PageHeader>

		<ImportList :project="name" @new-import="showNewImport = true" />

		<NewImportDialog v-model:open="showNewImport" :project="name" />
	</div>
</template>

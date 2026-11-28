<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { LoadingIndicator } from "frappe-ui";
import ReviewDashboard from "@/components/review/ReviewDashboard.vue";
import ReviewItem from "@/components/review/ReviewItem.vue";
import { sortIssues } from "@/components/review/meta";

const props = defineProps({
	importName: { type: String, required: true },
	busy: { type: Boolean, default: false },
	// The import page loads the review once and shares it with the tree's approve check.
	review: { type: Object, required: true },
});
const emit = defineEmits(["open-page"]);

const route = useRoute();
const router = useRouter();

const issues = computed(() => sortIssues(props.review.data?.issues || []));
const itemId = computed(() => route.query.item || null);

function openItem(id) {
	router.push({ query: { ...route.query, item: id } });
}

function closeItem() {
	const query = { ...route.query };
	delete query.item;
	router.push({ query });
}
</script>

<template>
	<div v-if="busy" class="flex flex-col items-center justify-center gap-3 py-24 text-center">
		<LoadingIndicator class="size-5 text-ink-gray-5" />
		<p class="text-base text-ink-gray-7">Review opens when processing finishes</p>
		<p class="text-sm text-ink-gray-5">Progress shows in the header above.</p>
	</div>
	<ReviewItem
		v-else-if="itemId"
		:import-name="importName"
		:issue-id="itemId"
		:queue="issues"
		@open="openItem"
		@close="closeItem"
		@changed="review.reload()"
	/>
	<ReviewDashboard
		v-else
		:import-name="importName"
		@changed="review.reload()"
		:review="review.data"
		:issues="issues"
		:loading="review.loading && !review.data"
		@open="openItem"
		@open-page="emit('open-page', $event)"
	/>
</template>

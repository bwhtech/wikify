<script setup>
import { Button } from "frappe-ui";
import AppPageHeader from "@/components/AppPageHeader.vue";
</script>

<template>
	<div>
		<AppPageHeader>
			<h1 class="text-md text-ink-gray-9">Page not found</h1>
		</AppPageHeader>
		<div class="flex flex-col items-center gap-3 px-4 py-24 text-center">
			<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
				<span class="lucide-file-question size-6" aria-hidden="true" />
			</div>
			<p class="text-base text-ink-gray-7">This page does not exist</p>
			<p class="text-sm text-ink-gray-5">The link may be old or mistyped.</p>
			<Button label="Go to projects" :route="{ name: 'Projects' }" />
		</div>
	</div>
</template>

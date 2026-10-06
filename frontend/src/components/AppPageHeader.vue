<script setup>
import { Button, PageHeader } from "frappe-ui";
import { useIsMobile } from "@/composables/useMediaQuery";
import { useTheme } from "@/utils/useTheme";

const isMobile = useIsMobile();
const { resolvedTheme, toggleTheme } = useTheme();
</script>

<template>
	<PageHeader>
		<template v-if="isMobile">
			<slot />
			<slot name="actions" />
		</template>
		<div v-else class="flex w-full items-center gap-3">
			<div class="flex min-w-0 flex-1 basis-0 items-center">
				<slot />
			</div>
			<div class="flex min-w-max flex-1 basis-0 items-center justify-end gap-2">
				<slot name="actions" />
				<Button
					variant="ghost"
					:icon="resolvedTheme === 'dark' ? 'lucide-sun' : 'lucide-moon'"
					:tooltip="resolvedTheme === 'dark' ? 'Light mode' : 'Dark mode'"
					:label="resolvedTheme === 'dark' ? 'Light mode' : 'Dark mode'"
					@click="toggleTheme"
				/>
			</div>
		</div>
	</PageHeader>
</template>
